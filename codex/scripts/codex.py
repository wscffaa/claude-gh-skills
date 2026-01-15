#!/usr/bin/env python3
"""Codex CLI wrapper with JSON streaming and auto-exit on turn.completed."""
import subprocess, sys, os, select, time, json, fcntl

DEFAULT_MODEL = os.environ.get('CODEX_MODEL', 'gpt-5.1-codex-max')
DEFAULT_TIMEOUT = 7200
POLL_INTERVAL = 0.1

def log_info(msg):
    sys.stderr.write(f"INFO: {msg}\n")
    sys.stderr.flush()

def log_error(msg):
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.stderr.flush()

def parse_args():
    if len(sys.argv) < 2:
        log_error('Prompt required')
        sys.exit(1)
    prompt, images, workdir = None, [], '.'
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ('-i', '--image'):
            if i + 1 < len(sys.argv):
                images.append(sys.argv[i + 1])
                i += 2
            else:
                log_error('-i requires path')
                sys.exit(1)
        elif arg in ('-r', '--reasoning-effort', '-p', '--progress'):
            if arg in ('-r', '--reasoning-effort') and i + 1 < len(sys.argv):
                i += 2
            else:
                i += 1
        elif arg.startswith('-'):
            log_error(f'Unknown: {arg}')
            sys.exit(1)
        elif prompt is None:
            prompt = arg
            i += 1
        else:
            workdir = arg
            i += 1
    if prompt is None:
        log_error('Prompt required')
        sys.exit(1)
    return {'prompt': prompt, 'images': images, 'workdir': workdir}

def build_cmd(images):
    cmd = ['codex', 'exec', '-m', DEFAULT_MODEL, '--json', '--dangerously-bypass-approvals-and-sandbox']
    for img in images:
        cmd.extend(['-i', img])
    return cmd
