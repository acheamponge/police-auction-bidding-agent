"""The Vercel entry point: every request to the site comes here.

vercel.json sends every path to this one function and passes the path the
visitor asked for as ?p=... The function serves the pages and the JSON API;
Vercel itself serves the files in public/.

Nothing is kept between requests: a new app is built for each one and filled
from the session the visitor's browser sends. See src/web.py.

Vercel's build looks in this file for a class declared exactly as
`class handler(BaseHTTPRequestHandler)`. Assigning a class to the name
`handler` is not enough: the build fails with PYTHON_ENTRYPOINT_NOT_FOUND.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isdir(os.path.join(PROJECT_DIR, "web")):
    PROJECT_DIR = os.getcwd()     # Vercel runs functions from the project root
sys.path.insert(0, PROJECT_DIR)

from src.web import answer_hosted  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 — the name Vercel looks for
    def do_GET(self):  # noqa: N802 — http.server's naming
        answer_hosted(self, "GET", PROJECT_DIR)

    def do_POST(self):  # noqa: N802
        answer_hosted(self, "POST", PROJECT_DIR)
