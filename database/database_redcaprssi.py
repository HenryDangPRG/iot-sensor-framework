import base64
import os
import numpy
import sys
import traceback
from database import Database
import queue
import threading
import os
from time import sleep
import pycurl
import json
import io


class REDCapRSSIDatabase(Database):
    def __init__(self, crypto, db_path='https://localhost', token='', dispatchsleep=0):
        Database.__init__(self, crypto, db_path=db_path, flush=False)
        self.token = token
        self.insertion_queue = queue.Queue()
        self.dispatcher_thread = threading.Thread(
            target=self.dispatcher, args=())
        self.dispatcher_thread.start()
        self.dispatchsleep = dispatchsleep

    # no db_decrypt because you can export from REDCap to CSV and then create a db from that
    def db_encrypt(self, s, counter):
        # counter = int(counter) % 10^16 # counter must be at most 16 digits
        counter = int(str(counter)[-self.crypto.MAX_COUNTER_DIGITS:])  # counter must be at most 16 digits, take rightmost 16 characters

        if type(s) is int:
            val = str(s)
        elif type(s) is float:
            val = str(s)
        else:
            val = s

        aes = self.crypto.get_db_aes(self.db_password, counter)
        padded = self.crypto.pad(val)
        enc = aes.encrypt(padded)
        b64enc = base64.b64encode(enc)
        return b64enc

    def redcap_dispatch(self, recordsdictlist):
        # encrypt on dispatch and add an ID which redcap requires
        for record in recordsdictlist:
            db_pw = record['db_pw']
            del record['db_pw']
            self.db_password = db_pw
            record['rssi'] = self.db_encrypt(
                record['rssi'], record['interrogatortime'])
            record['doppler'] = self.db_encrypt(
                record['doppler'], record['interrogatortime'])
            record['phase'] = self.db_encrypt(
                record['phase'], record['interrogatortime'])
            record['epc96'] = self.db_encrypt(
                record['epc96'], record['interrogatortime'])
            record['record_id'] = str(record['interrogatortime'])

        data = json.dumps(recordsdictlist)

        fields = {
            'token': self.token,
            'content': 'record',
            'format': 'json',
            'type': 'flat',
            'data': data,
        }

        buf = io.StringIO()
        ch = pycurl.Curl()
        ch.setopt(ch.URL, self.db_path)
        ch.setopt(ch.HTTPPOST, list(fields.items()))
        ch.setopt(ch.WRITEFUNCTION, buf.write)
        ch.setopt(pycurl.SSL_VERIFYPEER, 1)
        ch.setopt(pycurl.SSL_VERIFYHOST, 2)
        ch.perform()
        ch.close()

        #result = buf.getvalue()
        #print '***'
        #print '***'
        #print data
        #print result
        #print '***'
        #print '***'

    # dispatch insertions from the queue so that the webserver can continue receiving requests
    # log each request to the Audit
    def dispatcher(self):
        while 1:
            queuelist = []

            input_dict = self.insertion_queue.get(block=True)
            queuelist.append(input_dict)

            # http://stackoverflow.com/questions/156360/get-all-items-from-thread-queue
            # while we're here, try to pick up any more items that were inserted into the queue
            while 1:
                try:
                    input_dict = self.insertion_queue.get_nowait()
                    queuelist.append(input_dict)
                except queue.Empty:
                    break

            self.redcap_dispatch(queuelist)

            if self.dispatchsleep > 0:
                # if desired, sleep the dispatcher for a short time to queue up some inserts and give the producer some CPU time
                sleep(self.dispatchsleep)

    # just insert into a queue for the dispatcher to insert in the background
    def insert_row(self, relativetime, interrogatortime, freeform, db_pw=''):
        input_dict = dict()  # read by the consumer dispatcher
        input_dict['relativetime'] = relativetime
        input_dict['interrogatortime'] = interrogatortime
        input_dict['rssi'] = freeform['rssi']
        input_dict['epc96'] = freeform['epc96']
        input_dict['doppler'] = freeform['doppler']
        input_dict['phase'] = freeform['phase']
        input_dict['antenna'] = freeform['antenna']
        input_dict['rospecid'] = freeform['rospecid']
        input_dict['channelindex'] = freeform['channelindex']
        input_dict['tagseencount'] = freeform['tagseencount']
        input_dict['accessspecid'] = freeform['accessspecid']
        input_dict['inventoryparameterspecid'] = freeform['inventoryparameterspecid']
        input_dict['lastseentimestamp'] = freeform['lastseentimestamp']
        input_dict['db_pw'] = db_pw

        self.insertion_queue.put(input_dict)
