"""Test-only raw stream relay; never logs, parses or stores transferred bytes."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import threading

STOP = threading.Event()
ACTIVE = set()
LOCK = threading.Lock()


def on_signal(_signal, _frame):
    STOP.set()


def pump(source, target):
    try:
        while not STOP.is_set():
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def connection(client, mode, path):
    target = None
    try:
        if mode == 'host':
            target = socket.create_connection(('127.0.0.1', 17006), timeout=5)
            target.settimeout(None)
        else:
            target = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target.settimeout(5)
            target.connect(str(path))
            target.settimeout(None)
        with LOCK:
            ACTIVE.update([client, target])
        reverse = threading.Thread(target=pump, args=(target, client), daemon=True)
        reverse.start()
        pump(client, target)
        reverse.join()
    except OSError:
        pass
    finally:
        for item in [client, target]:
            if item is not None:
                with LOCK:
                    ACTIVE.discard(item)
                item.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['host', 'namespace'])
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--ready', type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    listener = socket.socket(socket.AF_UNIX if args.mode == 'host' else socket.AF_INET, socket.SOCK_STREAM)
    created_inode = None
    if args.mode == 'host':
        if args.socket.exists() or args.socket.is_symlink():
            raise RuntimeError('fixture-socket-already-exists')
        listener.bind(str(args.socket))
        os.chmod(args.socket, 0o600)
        created_inode = args.socket.stat().st_ino
    else:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 17006))
    listener.listen(32)
    listener.settimeout(.2)
    args.ready.write_text(json.dumps({'ready': True, 'mode': args.mode, 'payload_storage': False}) + '\n')
    args.ready.chmod(0o600)
    try:
        while not STOP.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            threading.Thread(target=connection, args=(client, args.mode, args.socket), daemon=True).start()
    finally:
        listener.close()
        with LOCK:
            active = list(ACTIVE)
        for item in active:
            try:
                item.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            item.close()
        if created_inode is not None and args.socket.exists() and args.socket.stat().st_ino == created_inode:
            args.socket.unlink()


if __name__ == '__main__':
    main()
