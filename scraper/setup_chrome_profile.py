"""
One-time setup: opens a dedicated Chrome profile so you can log into LinkedIn
manually. The login session is saved in this profile directory and reused by
linkedin_scraper.py — this avoids ever automating the LinkedIn login flow
itself, which is the part most likely to trigger anti-bot challenges.

Run this once (and again only if LinkedIn logs you out):
    python scraper/setup_chrome_profile.py
"""

from pathlib import Path                              # cross-platform file path handling

from selenium import webdriver                          # drives an actual Chrome browser
from selenium.webdriver.chrome.options import Options    # lets us configure how Chrome launches

PROFILE_DIR = Path(__file__).parent / ".chrome-profile"  # folder that will hold this dedicated profile's data (cookies, login session, etc.)


def main() -> None:
    PROFILE_DIR.mkdir(exist_ok=True)   # create the folder if it doesn't exist yet; exist_ok=True means "don't error if it's already there"

    # `Options` is Chrome's own launch configuration — think of it as the list
    # of command-line flags you'd pass if you started Chrome from a terminal.
    # Selenium builds this object, then hands it to webdriver.Chrome() below,
    # which actually launches the browser process with those flags applied.
    options = Options()                                                     # start with an empty set of launch flags
    options.add_argument(f"--user-data-dir={PROFILE_DIR.resolve()}")        # tell Chrome to store ALL its data (cookies, login sessions, cache, history) in our own folder instead of your normal Chrome profile
    options.add_argument("--profile-directory=Default")                    # within that user-data-dir, use the sub-profile named "Default" (Chrome supports multiple named profiles per user-data-dir; we only need one, so we just use the built-in default name)

    driver = webdriver.Chrome(options=options)   # actually launch a real, visible Chrome window using those options — `driver` is our remote-control handle to it
    driver.get("https://www.linkedin.com/login")  # tell that Chrome window to navigate to LinkedIn's login page

    print(f"Chrome profile dir: {PROFILE_DIR.resolve()}")
    print("Log into LinkedIn in the opened window.")
    input("Once you're logged in and see your LinkedIn feed, press Enter here to close... ")
    # input() pauses this Python script and waits for you to type something (or
    # just press Enter) in the terminal. This is what gives YOU time to
    # manually type your email/password into the Chrome window and log in —
    # the script does nothing during this pause, it's just waiting on you.

    driver.quit()   # close the Chrome window and end the browser process
    print("Done. Session saved. You can now run linkedin_scraper.py.")
    # Because Chrome was writing to PROFILE_DIR the whole time (via
    # --user-data-dir), the login cookies/session are now saved on disk there
    # permanently — that's what lets linkedin_scraper.py open a NEW Chrome
    # window later, pointed at the same folder, and find you already logged in.


if __name__ == "__main__":   # only run main() when this file is executed directly (e.g. `python setup_chrome_profile.py`), not if some other script imports it
    main()
