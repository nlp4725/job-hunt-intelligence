"""
One-time setup: opens a dedicated Chrome (or Edge) profile so you can log
into LinkedIn manually. The login session is saved in this profile directory
and reused by linkedin_scraper.py's build_driver() — this avoids ever
automating the LinkedIn login flow itself, which is the part most likely to
trigger anti-bot challenges.

Run this once per browser you plan to use (and again only if LinkedIn logs
you out of that browser's profile):
    python scraper/setup_chrome_profile.py            # Chrome (default)
    python scraper/setup_chrome_profile.py --browser edge
"""

import sys                                             # read --browser from argv
from pathlib import Path                              # cross-platform file path handling

from selenium import webdriver                          # drives an actual Chrome/Edge browser
from selenium.webdriver.chrome.options import Options as ChromeOptions    # lets us configure how Chrome launches
from selenium.webdriver.edge.options import Options as EdgeOptions        # lets us configure how Edge launches — same flag names as Chrome (both Chromium-based)

PROFILE_DIR = Path(__file__).parent / ".chrome-profile"       # folder that will hold Chrome's dedicated profile data (cookies, login session, etc.)
EDGE_PROFILE_DIR = Path(__file__).parent / ".edge-profile"    # separate folder for Edge's profile — must not share PROFILE_DIR, each browser needs its own login session


def main() -> None:
    browser = "edge" if "--browser" in sys.argv and "edge" in sys.argv else "chrome"
    profile_dir = EDGE_PROFILE_DIR if browser == "edge" else PROFILE_DIR
    profile_dir.mkdir(exist_ok=True)   # create the folder if it doesn't exist yet; exist_ok=True means "don't error if it's already there"

    # `Options` is the browser's own launch configuration — think of it as the
    # list of command-line flags you'd pass if you started Chrome/Edge from a
    # terminal. Selenium builds this object, then hands it to webdriver.Chrome()
    # or webdriver.Edge() below, which actually launches the browser process
    # with those flags applied.
    options = EdgeOptions() if browser == "edge" else ChromeOptions()       # start with an empty set of launch flags
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")        # tell the browser to store ALL its data (cookies, login sessions, cache, history) in our own folder instead of your normal profile
    options.add_argument("--profile-directory=Default")                    # within that user-data-dir, use the sub-profile named "Default" (both browsers support multiple named profiles per user-data-dir; we only need one, so we just use the built-in default name)

    driver = webdriver.Edge(options=options) if browser == "edge" else webdriver.Chrome(options=options)   # actually launch a real, visible browser window using those options — `driver` is our remote-control handle to it
    driver.get("https://www.linkedin.com/login")  # tell that window to navigate to LinkedIn's login page

    print(f"{browser.capitalize()} profile dir: {profile_dir.resolve()}")
    print("Log into LinkedIn in the opened window.")
    input("Once you're logged in and see your LinkedIn feed, press Enter here to close... ")
    # input() pauses this Python script and waits for you to type something (or
    # just press Enter) in the terminal. This is what gives YOU time to
    # manually type your email/password into the browser window and log in —
    # the script does nothing during this pause, it's just waiting on you.

    driver.quit()   # close the browser window and end the process
    print("Done. Session saved. You can now run linkedin_scraper.py "
          + ("(pass browser=\"edge\" to build_driver())." if browser == "edge" else "."))
    # Because the browser was writing to profile_dir the whole time (via
    # --user-data-dir), the login cookies/session are now saved on disk there
    # permanently — that's what lets linkedin_scraper.py open a NEW window
    # later, pointed at the same folder, and find you already logged in.


if __name__ == "__main__":   # only run main() when this file is executed directly (e.g. `python setup_chrome_profile.py`), not if some other script imports it
    main()
