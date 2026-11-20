"""Credential-free ingress checks; never certifies network isolation by timeout.

No pairing, printer, financial, Shopify or database command is performed. Normal
HTTP request rate counters may advance. See docs/print-agent-railway-ingress.md.
"""
import argparse,json
from urllib.parse import urlsplit
import httpx
from print_agent_security import public_host

DEFAULT_ORIGIN='https://pixiepinksledger-production.up.railway.app'
PATH='/api/print-agent/config'


def run_checks(client,origin,private_url=None):
    host=public_host(origin);origin=origin.rstrip('/')
    checks={}
    try:
        reply=client.get(origin+PATH)
        try:auth=reply.json()=={'detail':'Device authentication unavailable'}
        except ValueError:auth=False
        checks['public_https_auth']='PASS' if reply.status_code==401 and auth else 'FAIL'
        reply=client.get(origin+PATH,headers={'Origin':'https://untrusted.example'})
        checks['browser_origin']='PASS' if reply.status_code==403 else 'FAIL'
        reply=client.get('http://'+host+PATH,headers={'X-Forwarded-Proto':'https','Host':host})
        target=urlsplit(reply.headers.get('location',''))
        redirect=reply.status_code in {301,302,307,308} and target.scheme=='https' and target.hostname==host and target.port in {None,443} and not target.username and not target.password
        checks['public_http_spoof']='PASS' if reply.status_code==403 or redirect else 'FAIL'
    except (httpx.HTTPError,ValueError):checks['public_transport']='FAIL'
    if private_url:
        target=urlsplit(private_url)
        if target.scheme!='http' or not target.hostname or target.username or target.password or target.path not in {'','/'} or target.query or target.fragment:raise ValueError('Private probe requires an approved plain HTTP service origin without credentials/path')
        try:
            client.get(private_url.rstrip('/')+PATH,headers={'Host':host,'X-Forwarded-Proto':'https','X-Forwarded-Host':host,'X-Forwarded-Port':'443'})
            checks['private_network_path']='FAIL_REACHABLE'
        except httpx.TransportError:
            # A wrong address, DNS error or outage can look like isolation. Require
            # independent approved mapping/positive controls, not a false PASS.
            checks['private_network_path']='UNREACHABLE_NOT_CERTIFIED'
    else:checks['private_network_path']='NOT_TESTED'
    failed=any(value.startswith('FAIL') for value in checks.values())
    return {'status':'FAILED' if failed else 'OPERATOR_REVIEW_REQUIRED','checks':checks,'network_isolation_certified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-origin',default=DEFAULT_ORIGIN)
    parser.add_argument('--private-url',help='Only from an authorized untrusted-workload negative-control source')
    args=parser.parse_args()
    try:
        with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:result=run_checks(client,args.public_origin,args.private_url)
    except ValueError:parser.error('Invalid origin configuration; do not supply credentials or tokens')
    print(json.dumps(result,indent=2))
    return 1 if result['status']=='FAILED' else 2

if __name__=='__main__':raise SystemExit(main())
