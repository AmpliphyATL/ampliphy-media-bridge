"""Input adapters: turn whatever an automation system sends into a TrackEvent.

Each adapter only parses; it never resolves. The HTTP ones are wired into app.py,
the TCP one runs as a background thread when METABRIDGE_TCP_PORT is set.
"""
