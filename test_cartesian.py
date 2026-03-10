#!/usr/bin/env python3

from hebrew_format import format_rtl_text

# Cartesian product - all combinations where each position picks one element from its set.

# Define sets for each position
set1 = ['1', 'first', 'הראשון']
set2 = ['2', 'second', 'השני']
set3 = ['3', 'third', 'השלישי']

hebrew = {'הראשון', 'השני', 'השלישי'}

# Generate Cartesian product using three loops
for a in set1:
    for b in set2:
        for c in set3:
            if a in hebrew or b in hebrew or c in hebrew:
                text = f"{a} {b} {c}"
                print(text)
                print(format_rtl_text(text, 80))
                print()
