#!/usr/bin/env python3

from hebrew_format import format_rtl_text

# Cartesian product - all combinations where each position picks one element from its set.

# Define sets for each position
set1 = ['1', 'first', 'הראשון']
set2 = ['2', 'second', 'השני']

hebrew = {'הראשון', 'השני'}

# Generate Cartesian product using three loops
for a in set1:
    for b in set2:
        if a in hebrew or b in hebrew:
            text = f"{a} {b}"
            print(text)
            print(format_rtl_text(text, 80))
            print()
