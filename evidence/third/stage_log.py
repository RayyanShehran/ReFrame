"""Log a project's generation stages with times (seconds since this script started) until it
stops running. Starts logging immediately (the UI starts generation together with project setup).

Usage: python stage_log.py <project-id> <out.txt>
"""
import json, sys, time, urllib.request

pid, out = sys.argv[1], sys.argv[2]
url = f"http://127.0.0.1:8000/api/projects/{pid}/generation"
get = lambda: json.load(urllib.request.urlopen(url, timeout=30))
began, stage, status, lines = time.monotonic(), None, None, []
while True:
    state = get()
    if (state["stage"], state["status"]) != (stage, status):
        stage, status = state["stage"], state["status"]
        lines.append(f"{time.monotonic() - began:7.1f} s  {status:8} {stage}")
        print(lines[-1], flush=True)
    if status not in {"running", "idle"} or time.monotonic() - began > 3600:
        break
    time.sleep(0.5)
lines.append("notices: " + " | ".join(state.get("notices") or []))
lines.append("message: " + str(state.get("message")))
open(out, "w").write("\n".join(lines) + "\n")
