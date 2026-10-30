"""Obtain one pinned public archive; analysis and raw-source publication are separate."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


class BoundedRedirects(HTTPRedirectHandler):
    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum
        self.steps = []

    def redirect_request(self, request, response, code, message, headers, new_url):
        if len(self.steps) >= self.maximum or urlparse(new_url).scheme != 'https':
            raise RuntimeError('Redirect count or HTTPS source gate failed')
        self.steps.append({'status': code, 'from_host': urlparse(request.full_url).hostname,
                           'to_host': urlparse(new_url).hostname})
        return super().redirect_request(request, response, code, message, headers, new_url)


def capture(output, receipt_path, protocol_commit, expected_sha256=None):
    plan = json.loads((ROOT / 'data/source_plan.json').read_text(encoding='utf-8'))['file']
    output = output.resolve()
    receipt_path = receipt_path.resolve()
    partial = output.with_suffix(output.suffix + '.partial')
    if output.exists() or partial.exists() or receipt_path.exists():
        raise FileExistsError('Refusing to replace an existing source capture or receipt')
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'status': 'STARTED', 'source_url': plan['url'], 'filename': plan['name'],
               'protocol_commit': protocol_commit, 'started_utc': datetime.now(timezone.utc).isoformat(),
               'runtime': {'python': platform.python_version(), 'implementation': platform.python_implementation()},
               'expected_bytes': plan['bytes'], 'publisher_md5': plan['publisher_md5']}

    def save():
        receipt_path.write_bytes((json.dumps(receipt, indent=2) + '\n').encode('utf-8'))

    save()
    redirects = BoundedRedirects(plan['maximum_redirects'])
    try:
        current = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        receipt['capture_code_commit'] = current
        subprocess.run(['git', 'merge-base', '--is-ancestor', protocol_commit, current], cwd=ROOT, check=True)
        request = Request(plan['url'], headers={'User-Agent': 'ResearchPortfolio-SourceCapture/1.0 (independent reproducibility research)'})
        md5, sha = hashlib.md5(), hashlib.sha256()
        count = 0
        with build_opener(redirects).open(request, timeout=60) as response, partial.open('xb') as handle:
            receipt.update(http_status=response.status, final_host=urlparse(response.url).hostname,
                           headers={k: response.headers.get(k) for k in ['Content-Type', 'Content-Length', 'ETag', 'Last-Modified']})
            if response.status != 200:
                raise RuntimeError('Source did not return HTTP200')
            while True:
                block = response.read(65536)
                if not block:
                    break
                count += len(block)
                if count > plan['maximum_bytes']:
                    raise RuntimeError('Source exceeded the fixed byte bound')
                handle.write(block)
                md5.update(block)
                sha.update(block)
        receipt.update(bytes=count, md5=md5.hexdigest(), sha256=sha.hexdigest(), redirects=redirects.steps)
        if count != plan['bytes'] or md5.hexdigest() != plan['publisher_md5']:
            raise RuntimeError('Publisher size/checksum source gate failed')
        if expected_sha256 is not None and sha.hexdigest() != expected_sha256:
            raise RuntimeError('Recorded SHA256 source gate failed')
        partial.rename(output)
        receipt.update(status='PASS_PINNED_SOURCE_CAPTURE', finished_utc=datetime.now(timezone.utc).isoformat())
        save()
        print(json.dumps({k: receipt[k] for k in ['status', 'bytes', 'md5', 'sha256', 'protocol_commit', 'http_status', 'final_host']}))
    except Exception as error:
        receipt.update(status='FAILED_SOURCE_CAPTURE_NO_RETRY', finished_utc=datetime.now(timezone.utc).isoformat(),
                       error_type=type(error).__name__, redirects=redirects.steps)
        if isinstance(error, HTTPError):
            receipt['http_status'] = error.code
        save()
        print(json.dumps({'status': receipt['status'], 'error_type': type(error).__name__, 'http_status': receipt.get('http_status')}))
        raise RuntimeError('Source capture stopped; preserve receipt and any partial bytes') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--protocol-commit', required=True)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    capture(args.out, args.receipt, args.protocol_commit, args.expected_sha256)
