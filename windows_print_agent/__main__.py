"""Run `python -m windows_print_agent --help` on the paired Windows user account."""
import argparse,getpass,json,os,sys,time
from pathlib import Path
from uuid import UUID
from .transport import Transport,EncryptedTransport,AgentError
from .windows import WindowsPrinters
from .engine import Engine
from .store import Store
from .protocol import *
from .diagnostics import call


def protect(path,value):
    import win32crypt
    data=json.dumps(value).encode();blob=win32crypt.CryptProtectData(data,'PixiePinks Print Agent',None,None,None,0)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(blob);os.replace(temporary,path)

def load(path):
    import win32crypt
    return json.loads(win32crypt.CryptUnprotectData(path.read_bytes(),None,None,None,0)[1])

def confirm(prompt):return input(prompt+' Type YES: ').strip()=='YES'

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['pair','printers','attest','run','status','forget']);parser.add_argument('--server');parser.add_argument('--public-key',help='Pinned non-secret public key copied from authenticated CRM Admin')
    args=parser.parse_args()
    if os.name!='nt':parser.error('Windows 11 with the configured printer drivers is required')
    root=Path(os.environ['LOCALAPPDATA'])/'PixiePinks'/'PrintAgent';root.mkdir(parents=True,exist_ok=True)
    secret=root/'credentials.dpapi';attestation=root/'attestation.dpapi'
    try:
        if args.command=='forget':
            if not confirm('Revoke this device in CRM first. Forget local credentials (retain action journal)?'):return 1
            secret.unlink(missing_ok=True);attestation.unlink(missing_ok=True);return 0
        if args.command=='pair':
            if not args.server:raise AgentError('Pairing requires --server https://your-approved-crm-host')
            transport=EncryptedTransport(args.server,args.public_key) if args.public_key else Transport(args.server);code=getpass.getpass('One-time CRM pairing code: ');token=transport.pair(code)
            did=str(UUID(token.split('.',1)[0]));protect(secret,{'server':transport.server,'token':token,'device_id':did,'public_key':args.public_key});print('Paired securely. Activate the device in CRM, then attest local printer settings.');return 0
        backend=WindowsPrinters()
        if args.command=='printers':
            rows=backend.status(backend.enumerate())
            for row in rows:
                try:
                    details=backend.info(row['name']);row.update(driver=details['pDriverName'],port=details['pPortName'],windows_status_mask=details['Status'])
                except Exception:pass
            print(json.dumps(rows,indent=2));return 0
        credentials=load(secret);transport=EncryptedTransport(credentials['server'],credentials['public_key'],credentials['token']) if credentials.get('public_key') else Transport(credentials['server'],credentials['token'])
        # Startup/network recovery: run retries connectivity without any print retry.
        if args.command=='run':
            while True:
                try:config=transport.config();break
                except KeyboardInterrupt:return 0
                except Exception:
                    print('Connector waiting for verified server/device connectivity; no print attempted.',flush=True);time.sleep(5)
        else:config=transport.config()
        names=Engine.names(config)
        if args.command=='status':
            bindings=[]
            for name in names:
                try:
                    row=backend.info(name);bindings.append({'queue':name,'driver':row['pDriverName'],'port':row['pPortName'],'windows_status_mask':row['Status']})
                except Exception:bindings.append({'queue':name,'binding':'UNAVAILABLE'})
            print(json.dumps({'device':credentials['device_id'],'printers':backend.status(names),'bindings':bindings,'local_attestation_present':attestation.exists()},indent=2));return 0
        if args.command=='attest':
            with backend.exclusive():
                if any(q['jobs'] or q['state']!='READY' for q in backend.status(names)):raise AgentError('All configured queues must be empty and ready before attestation')
                if not backend.shares_port([RECEIPT_QUEUE] if config['config'].get('receipt_only') else [RECEIPT_QUEUE,LABEL_QUEUE]+([DRAWER_QUEUE] if config['config']['drawer_queue'] else [])):raise AgentError('Configured queues must use their verified local USB port')
                if not confirm('All queues used by this connector have Cash Drawer=None. Confirm in Windows preferences'):return 1
                if not confirm('80 mm receipt geometry/feed/cutter were physically tested; Cash Drawer=None' if config['config'].get('receipt_only') else 'Paper geometry and cutter were physically tested; label mode cannot open the drawer'):return 1
                isolated=False;tested=False;no_replay=False
                if config['config']['drawer_queue']:
                    if not backend.drawer_safe(DRAWER_QUEUE):raise AgentError('Isolated drawer queue must use direct printing with no document retention')
                    isolated=confirm('Dedicated drawer queue uses the tested POS-80 driver and is restricted to the agent Windows account')
                    tested=confirm('Manufacturer-supported driver Cashbox #1 after-print behavior was physically verified on ONLY that queue; key is Under Control')
                    no_replay=confirm('Supported driver/direct printing, no document retention, and interrupted USB/spooler recovery have been verified not to replay a cashbox job; unsupported or unknown means NO')
                    if not isolated or not tested or not no_replay:return 1
                value={'device_id':credentials['device_id'],'normal_drawers_none':True,'media_and_cutter_tested':True,'drawer_queue_isolated':isolated,'drawer_driver_tested':tested,'drawer_no_replay_tested':tested and no_replay if config['config']['drawer_queue'] else False,'fingerprints':{name:backend.fingerprint(name) for name in names}}
                protect(attestation,value);print('Local driver settings attested. This command sent no print or drawer action.');return 0
        if args.command=='run':
            # One process owns the physical device; SQLite action fences survive process/OS restart.
            with backend.exclusive():
                store=call('journal_open',Store,root/'actions.sqlite');proof=call('attestation_load',load,attestation);engine=Engine(transport,backend,store,proof,credentials['device_id'])
                try:
                    while True:
                        try:engine.cycle()
                        except KeyboardInterrupt:break
                        except Exception:print('Agent waiting: connection, printer, authorization or local verification needs review.',flush=True)
                        time.sleep(5)
                finally:call('journal_close',store.close)
            return 0
    except KeyboardInterrupt:return 0
    except Exception:
        print('Agent unavailable. Check CRM activation, pairing and local driver attestation. No credentials are logged.',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
