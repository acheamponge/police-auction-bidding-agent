"""The Vercel entry point: every request to the site comes here.

vercel.json sends every path to this one function and passes the path the
visitor asked for as ?p=... The function serves the pages and the JSON API;
Vercel itself serves the files in public/.

Nothing is kept between requests: a new app is built for each one and filled
from the session the visitor's browser sends. See src/web.py.
"""

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isdir(os.path.join(PROJECT_DIR, "web")):
    PROJECT_DIR = os.getcwd()     # Vercel runs functions from the project root
sys.path.insert(0, PROJECT_DIR)

from src.web import make_hosted_handler  # noqa: E402

handler = make_hosted_handler(PROJECT_DIR)
