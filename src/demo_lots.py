"""Demo lots: real PropertyRoom lots, verified live on Nov 23, 2026.

These are recently-ended lots whose pages still carry the full fee table —
the same fields the agent reads on a live lot (a live lot shows a countdown
where these show "Ended ..."). The demo fetches each page live at run time;
pass --lot <url> to evaluate any other lot, including a live one.

1. 2019 Ford F-150 Police Responder (Hartford, CT) — 12% premium, $10,850
   final, $50 increment, pickup $0. Capital-asset premium, wire threshold.
2. 2014 Chevrolet Caprice (Hermitage, PA) — 15% premium, RESERVE NOT MET,
   $4,450 final, $50 increment. The soft-reserve trap, live in the data.
3. 2007 Toyota Prius (Jamaica, NY) — 15% premium, $800 final, $50
   increment. Everyday lot, low price tier.
"""

DEMO_LOTS = [
    {
        "url": "https://www.propertyroom.com/l/2019-ford-f-150-police-responder-hartford-ct-06114/19007650",
        "label": "2019 Ford F-150 Police Responder — 12% premium, ended $10,850",
        "all_in_max": 13_500.00,
    },
    {
        "url": "https://www.propertyroom.com/l/2014-chevrolet-caprice-hermitage-pa-16148/17254881",
        "label": "2014 Chevrolet Caprice — 15% premium, RESERVE NOT MET, ended $4,450",
        "all_in_max": 6_000.00,
    },
    {
        "url": "https://www.propertyroom.com/l/2007-toyota-prius-jamaica-ny-11430/18690397",
        "label": "2007 Toyota Prius — 15% premium, ended $800",
        "all_in_max": 1_500.00,
    },
]
