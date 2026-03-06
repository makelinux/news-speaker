#!/usr/bin/env python3

import re

rli = '\u2066'  # Right-to-Left Isolate
lri = '\u2067'  # Left-to-Right Isolate
pdi = '\u2069'  # Pop Directional Isolate
rlm = '\u200f'  # Right-to-Left Mark


def format_hebrew_title(title, width=80):
    """Format Hebrew title for printing to console"""
    t = title.replace('"', '״')
    t = t.replace('?', f'?{rlm}')  # Add RLM after question mark
    t = re.sub(r'([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)', rf'{lri}\1{pdi}', t)
    num_marks = len(t) - len(title)
    t_rj = f"{rlm}{t}".rjust(width + num_marks + 1)  # Add RLM at start
    return f"{rli}{pdi}{t_rj}"
