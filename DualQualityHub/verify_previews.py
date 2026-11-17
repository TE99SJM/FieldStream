"""With the hub running in demo mode, verify actual WHEP video decoding in Chrome."""
import argparse
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import sync_playwright

from settings import cameras


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))
    for _ in range(60):
        try:
            with opener.open(args.url + "/api/status", timeout=3):
                break
        except (OSError, URLError):
            time.sleep(.5)
    else:
        raise SystemExit("Dashboard did not start")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width":390,"height":844})
        page.on("pageerror", lambda err: print("Browser error:", err))
        page.on("response", lambda r: print(r.status, r.url) if "/preview/" in r.url and r.status>=400 else None)
        try:
            page.goto(args.url)
            page.wait_for_function("""n => [...document.querySelectorAll('video')].filter(v =>
                v.videoHeight===480 && v.currentTime>.3 && v.readyState>=2).length===n""",
                arg=len(cameras()), timeout=90000)
            assert page.evaluate("document.documentElement.scrollWidth===innerWidth")
            print("All camera tiles decoded 480-line moving video in a mobile-sized viewport.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
