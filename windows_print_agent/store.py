"""Permanent local at-most-once fences, including uncertain actions after a crash."""
import hashlib,json,sqlite3
from pathlib import Path
from .diagnostics import recovered_intents

class Store:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,identity TEXT NOT NULL,claim TEXT NOT NULL,state TEXT NOT NULL,spool_id INTEGER,acked INTEGER NOT NULL DEFAULT 0)')
        # Even a crash before I/O may have reached a driver: never guess and replay.
        recovered=self.db.execute("UPDATE jobs SET state='UNKNOWN' WHERE state='INTENT'").rowcount;self.db.commit()
        recovered_intents(recovered)
    @staticmethod
    def identity(job):return hashlib.sha256(json.dumps({k:job[k] for k in ['id','device_id','version','kind','queue','media','sha256','size']},sort_keys=True).encode()).hexdigest()
    def get(self,jid):
        row=self.db.execute('SELECT identity,state FROM jobs WHERE id=?',(jid,)).fetchone();return row
    def intent(self,job):
        with self.db:self.db.execute("INSERT INTO jobs(id,device_id,identity,claim,state) VALUES(?,?,?,?,'INTENT')",(job['id'],job['device_id'],self.identity(job),job['claim']))
    def finish(self,jid,status,spool_id=None):
        with self.db:self.db.execute('UPDATE jobs SET state=?,spool_id=? WHERE id=?',(status,spool_id,jid))
    def pending(self,device_id):return self.db.execute('SELECT id,claim,state,spool_id FROM jobs WHERE device_id=? AND acked=0 AND state!=?',(device_id,'INTENT')).fetchall()
    def acked(self,jid):
        with self.db:self.db.execute('UPDATE jobs SET acked=1 WHERE id=?',(jid,))
    def close(self):self.db.close()
