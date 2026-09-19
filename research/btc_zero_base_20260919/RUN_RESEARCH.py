"""Canonical launcher: decode the public-data source and apply the verified
pandas column-name compatibility fix. No credentials or order endpoints.
Usage: python research/btc_zero_base_20260919/RUN_RESEARCH.py --out output
"""
import ast, base64, hashlib, runpy, sys, zlib
from pathlib import Path
here = Path(__file__).resolve().parent
parsed = ast.parse((here / 'research.py').read_text())
values = {}
for node in parsed.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
        for target in node.targets:
            if isinstance(target, ast.Name):
                values[target.id] = node.value.value
payload = values['PAYLOAD'].replace('KZbMfvW6uMR5', 'KZbMfvW6vMR5')
original = zlib.decompress(base64.b64decode(payload))
assert hashlib.sha256(original).hexdigest() == values['SOURCE_SHA256']
# DataFrame.hist is a method: column lookup must use square brackets.
source = original.decode().replace('x.hist', "x['hist']").encode()
assert hashlib.sha256(source).hexdigest() == '0ef26003314d2694ce574116a7674cd0f8cc08ad60ae9757327189976b3f2a66'
out = Path(sys.argv[sys.argv.index('--out')+1]) if '--out' in sys.argv else Path('output')
out.mkdir(parents=True, exist_ok=True)
canonical = out / 'research_source.py'
canonical.write_bytes(source)
(out / 'research_source.sha256').write_text(hashlib.sha256(source).hexdigest()+'\n')
runpy.run_path(str(canonical.resolve()), run_name='__main__')
