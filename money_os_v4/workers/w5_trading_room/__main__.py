import argparse,json
from pathlib import Path
from .worker import evaluate

p=argparse.ArgumentParser(description='W5 BTC daily shadow preview; no trading capability')
p.add_argument('--request',required=True);p.add_argument('--facts',required=True)
p.add_argument('--release',default=str(Path(__file__).resolve().parents[2]/'contracts/w5_btc_daily_release.json'))
a=p.parse_args()
read=lambda x:json.loads(Path(x).read_text())
print(json.dumps(evaluate(read(a.request),read(a.facts),read(a.release)),indent=2,ensure_ascii=False))
