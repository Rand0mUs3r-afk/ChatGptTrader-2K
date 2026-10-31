"""Persistent discovery-only state branch. Never changes the checked-out code branch."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DIR = Path(".discovery-state")
REF = "refs/heads/discovery-state"


def git(*args, text=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="github-actions[bot]",
        GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
        GIT_COMMITTER_NAME="github-actions[bot]", GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com")
    if env.get("GH_TOKEN"):
        i = int(env.get("GIT_CONFIG_COUNT", "0"))
        env.update(GIT_CONFIG_COUNT=str(i+1))
        env["GIT_CONFIG_KEY_"+str(i)] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_"+str(i)] = "AUTHORIZATION: basic "+base64.b64encode(("x-access-token:"+env["GH_TOKEN"]).encode()).decode()
    result = subprocess.run(["git", *args], input=text, capture_output=True, text=True, env=env, timeout=60)
    if result.returncode: raise RuntimeError("Discovery state git operation failed")
    return result.stdout.strip()


def fresh(document, now):
    if document.get("version") != 1 or not isinstance(document.get("entries"), list): raise ValueError("Invalid state schema")
    result = {}
    for row in document["entries"]:
        try:
            symbol = row["symbol"]
            observed = datetime.fromisoformat(row["observed_at"])
            expiry = datetime.fromisoformat(row["expires_at"])
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol): continue
            if observed.tzinfo is None or expiry.tzinfo is None: continue
            if 0 <= (now-observed).total_seconds() <= 30*86400 and now < expiry and (expiry-observed).total_seconds() <= 30*86400:
                result[symbol] = row
        except (KeyError, TypeError, ValueError): continue
    return list(result.values())[:150]


def read(now):
    path = DIR/"watchlist_auto.json"
    if not path.exists(): return []
    if path.stat().st_size > 2000000: raise ValueError("Oversize discovery state")
    return fresh(json.loads(path.read_text()), now)


def load():
    DIR.mkdir(exist_ok=True)
    tip = git("ls-remote", "origin", REF)
    if not tip:
        (DIR/"parent").write_text("")
        (DIR/"watchlist_auto.json").write_text(json.dumps(dict(version=1, entries=[])))
        return
    sha = tip.split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha): raise ValueError("Invalid state ref")
    git("fetch", "--no-tags", "origin", REF)
    text = git("show", sha+":watchlist_auto.json")
    fresh(json.loads(text), datetime.now(timezone.utc))
    (DIR/"parent").write_text(sha)
    (DIR/"watchlist_auto.json").write_text(text+"\n")


def save():
    parent = (DIR/"parent").read_text().strip()
    if parent and not re.fullmatch(r"[0-9a-f]{40}", parent): raise ValueError("Invalid parent")
    lines = []
    for name in ("watchlist_auto.json", "watchlist_auto.txt"):
        blob = git("hash-object", "-w", str(DIR/name))
        lines.append("100644 blob "+blob+"\t"+name+"\n")
    tree = git("mktree", text="".join(lines))
    args = ["commit-tree", tree]+(["-p", parent] if parent else [])
    sha = git(*args, text="Refresh Yahoo discovery watchlist\n")
    git("push", "origin", sha+":"+REF)


if __name__ == "__main__":
    try:
        if sys.argv[1] == "load": load()
        elif sys.argv[1] == "save": save()
        else: raise ValueError("Unknown state operation")
    except Exception as exc:
        if sys.argv[1:] == ["load", "--optional"]:
            DIR.mkdir(exist_ok=True)
            (DIR/"watchlist_auto.json").unlink(missing_ok=True)
            print(json.dumps(dict(discovery_state="UNAVAILABLE_NORMAL_SCAN_CONTINUES", error_type=type(exc).__name__)))
        else:
            raise SystemExit("Discovery state failed; details withheld") from None
