"""Write VAPID configuration to a new owner-only file; never print private keys."""
import argparse
import base64
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New private configuration file; existing files are never overwritten')
    parser.add_argument('--subject', required=True, help='Contact address, e.g. mailto:operations@example.com')
    args = parser.parse_args()
    if not args.subject.startswith(('mailto:', 'https://')) or any(ch in args.subject for ch in '\r\n'):
        parser.error('Use a mailto: address or HTTPS contact URL')
    private = ec.generate_private_key(ec.SECP256R1())
    encode = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
    public = encode(private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    secret = encode(private.private_numbers().private_value.to_bytes(32, 'big'))
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(f'STAFF_PUSH_ENABLED=false\nSTAFF_PUSH_VAPID_PUBLIC_KEY={public}\nSTAFF_PUSH_VAPID_PRIVATE_KEY={secret}\nSTAFF_PUSH_VAPID_SUBJECT={args.subject}\n')
    print('Created private VAPID configuration. Store it securely; do not commit or paste it into chat.')


if __name__ == '__main__':
    main()
