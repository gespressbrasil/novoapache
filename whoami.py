import sys
import flask

def run_test():
    version = sys.version
    exe = sys.executable
    paths = sys.path
    flask_loc = flask.__file__
    return f"""
    sys.version = {version}
    sys.executable = {exe}
    sys.path = {paths}
    flask.__file__ = {flask_loc}
    """

print(run_test())
