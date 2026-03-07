#!/usr/bin/env python3

import re

rli = '\u2066'  # Right-to-Left Isolate
lri = '\u2067'  # Left-to-Right Isolate
pdi = '\u2069'  # Pop Directional Isolate
rlm = '\u200f'  # Right-to-Left Mark


def visible_len(s):
    """Calculate visible length of string with control chars"""
    # Remove ANSI escape sequences (length 9+ bytes, 0 visible)
    s = re.sub(r'\x1b\[[0-9;]*m', '', s)
    # Count backspaces (1 byte, -1 visible)
    bs_count = s.count('\b')
    # Remove Unicode directional formatting chars
    s = re.sub(r'[\u2066-\u2069\u200e-\u200f]', '', s)
    return len(s) - 2 * bs_count


def add_rtl_marks(text):
    """Add RTL marks and isolate Latin text"""
    t = text.replace('"', '״')
    t = t.replace('?', f'?{rlm}')
    t = re.sub(r'([A-Za-z0-9]+(?:[-:][A-Za-z0-9]+)*)', rf'{lri}\1{pdi}', t)
    return t


def format_rtl_text(text, width=80):
    """Right-align RTL text for console output"""
    t = add_rtl_marks(text)
    vis_len = visible_len(t)
    t_rj = f"{rlm}{t}".rjust(width + len(t) - vis_len + 1)
    return f"{rli}{pdi}{t_rj}"
