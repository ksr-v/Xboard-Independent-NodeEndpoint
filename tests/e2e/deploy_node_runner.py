#!/usr/bin/env python3
"""Upload only public runner/tool source to the authorized two test VMs.

No credentials/keys/manifests, assets, Node processes, services, network rules
or panel data are touched. Files travel over strict-known-host SSH stdin,
receive an independent SHA256 check, and land in a Root-owned /opt lab tree.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
FILES = ("tests/e2e/node_runner.py", "tests/e2e/test_node_runner.py", "tests/e2e/admin_node_sync.py",
         "tools/node/node_endpoint.py", "tools/node/strict_yaml.py", "tools/node/release-lock.json")

REMOTE = r'''
import base64,hashlib,json,os,pathlib,py_compile,re,stat,sys,tempfile
def check(value,reason):
    if not value: raise RuntimeError(reason)
def guarded(path):
    for item in [path]+list(path.parents):
        if item.exists():
            info=item.lstat()
            check(not stat.S_ISLNK(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,"unsafe_root_ancestry")
root=pathlib.Path(sys.argv[1])
check(re.fullmatch(r"/opt/node-endpoint-[A-Za-z0-9_-]+(?:/source)?",str(root)),"destination_not_lab_tree")
check(root.is_dir(),"parent_must_create_root_lab_first")
guarded(root)
raw=sys.stdin.buffer.read(2*1024*1024+1)
check(len(raw)<=2*1024*1024,"source_bundle_too_large")
bundle=json.loads(raw)
allow={"tests/e2e/node_runner.py","tests/e2e/test_node_runner.py","tests/e2e/admin_node_sync.py","tools/node/node_endpoint.py","tools/node/strict_yaml.py","tools/node/release-lock.json"}
check(bundle.get("schema")==1 and isinstance(bundle.get("files"),list) and {row["path"] for row in bundle["files"]}==allow and len(bundle["files"])==len(allow),"source_allowlist_mismatch")
verified=[]
for row in bundle["files"]:
    data=base64.b64decode(row["data"],validate=True)
    check(hashlib.sha256(data).hexdigest()==row["sha256"],"transport_sha256_mismatch")
    target=root/row["path"]
    check(not target.is_symlink(),"symlink_target_refused")
    guarded(target.parent)
    if target.exists():
        info=target.stat()
        check(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==0 and not info.st_mode&0o022,"unsafe_existing_source")
    verified.append((target,data,row["sha256"],row["path"]))
for target,data,expected,relative in verified:
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    guarded(target.parent)
    fd,temp=tempfile.mkstemp(prefix=".node-e2e-source-",dir=target.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
        check(hashlib.sha256(target.read_bytes()).hexdigest()==expected,"installed_source_sha256_mismatch")
    finally:
        if os.path.exists(temp):os.unlink(temp)
for target,data,expected,relative in verified:
    if target.suffix==".py":py_compile.compile(str(target),doraise=True)
print(json.dumps({"result":"PASS","source_only":True,"files":[{"path":relative,"sha256":expected} for target,data,expected,relative in verified]},separators=(",",":")))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, choices=["192.168.128.128", "192.168.128.129"])
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--destination", required=True, help="existing Root-owned /opt/node-endpoint-* lab repository root")
    args = parser.parse_args()
    bundle = {"schema": 1, "files": []}
    for relative in FILES:
        data = (REPOSITORY / relative).read_bytes()
        bundle["files"].append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "data": base64.b64encode(data).decode()})
    remote_command = "sudo -n python3 -c " + shlex.quote(REMOTE) + " " + shlex.quote(args.destination)
    try:
        process = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
                                  "UserKnownHostsFile=" + str(Path(args.known_hosts).resolve()), "codex@" + args.host, remote_command],
                                 input=json.dumps(bundle).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False)
        if process.returncode != 0:
            # Remote exception messages/transport diagnostics are not public.
            print(json.dumps({"result": "FAIL", "reason": "source_deployment_or_compile_failed"}))
            return 1
        result = json.loads(process.stdout)
        if result.get("result") != "PASS" or result.get("source_only") is not True or {row["path"] for row in result["files"]} != set(FILES):
            raise ValueError()
        expected = {row["path"]: row["sha256"] for row in bundle["files"]}
        if any(expected[row["path"]] != row["sha256"] for row in result["files"]):
            raise ValueError()
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except BaseException:
        print(json.dumps({"result": "FAIL", "reason": "source_deployment_transport_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
