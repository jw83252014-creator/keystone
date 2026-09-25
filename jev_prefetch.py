#!/usr/bin/env python3
"""Fill jevloop's JevCache in parallel, using exactly the requests replay() will ask.

Every tick in a >=5s-spaced ticks.csv is a decision; Jev's question carries no position,
so request = JevDecider.request(facts(t, None, prev)) with prev = the previous tick of the
same market. Keys match JevCache. Resumable: answers already cached are skipped.
Usage: jev_prefetch.py ticks.csv jev.jsonl [threads]
"""
import hashlib, json, sys, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import jevloop

ticks_path, cache_path = sys.argv[1], sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 16

done = set()
try:
    for line in open(cache_path):
        done.add(json.loads(line)["key"])
except FileNotFoundError:
    pass

by_ticker = defaultdict(list)
for t in jevloop.load_csv(ticks_path):
    by_ticker[t.ticker].append(t)
todo = {}
for ts in by_ticker.values():
    ts.sort(key=lambda x: x.ts)
    prev = None
    for t in ts:
        req = jevloop.JevDecider.request(jevloop.facts(t, None, prev))
        key = hashlib.sha1(json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()
        if key not in done:
            todo[key] = req
        prev = t
print(f"{len(done)} cached, {len(todo)} to fetch", flush=True)

lock = threading.Lock()
n = errors = 0
with open(cache_path, "a") as fh, ThreadPoolExecutor(threads) as pool:
    futs = {pool.submit(jevloop.call_jev, req): key for key, req in todo.items()}
    for fut in as_completed(futs):
        try:
            ans = fut.result()
        except Exception:
            errors += 1
            continue
        with lock:
            fh.write(json.dumps({"key": futs[fut], "answer": ans}) + "\n")
            n += 1
            if n % 1000 == 0:
                fh.flush()
                print(f"{n}/{len(todo)} fetched, {errors} errors", flush=True)
print(f"done: {n} fetched, {errors} errors", flush=True)
