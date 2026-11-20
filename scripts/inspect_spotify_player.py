"""Read-only Brave player/DRM diagnostics; never sends play or login input."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import browser as browser_module
from browser import BrowserController, browser_config


def inspect(report_path, screenshot_path, ignored_args):
    launch_options = browser_module._launch_options

    def diagnostic_options(config, headless):
        options = launch_options(config, headless)
        options["ignore_default_args"].extend(ignored_args)
        return options

    browser_module._launch_options = diagnostic_options
    browser = BrowserController(browser="brave", headless=False)
    categories = []
    terms = (
        "widevine",
        "drm",
        "eme",
        "cdm",
        "autoplay",
        "license",
        "media",
        "player",
        "login",
    )

    def watch():
        page = browser._ensure_page()

        def record(message):
            text = message.text.casefold()
            found = [term for term in terms if term in text]
            if found:
                categories.append({"type": message.type, "terms": found})

        page.on("console", record)

    try:
        browser._worker.run(watch)
        browser.navigate(
            "https://open.spotify.com/search/Aaya%20Sher%20Anirudh%20Ravichander/tracks"
        )
        browser._worker.run(lambda: browser._require_page().wait_for_timeout(8000))
        result = browser.evaluate("""async () => {
            const visible = node => !!(node.getClientRects().length) && getComputedStyle(node).visibility !== 'hidden';
            const frameStart=performance.now();
            const frameClock=await Promise.race([
                new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now()-frameStart)))),
                new Promise(resolve=>setTimeout(()=>resolve('timed-out'),3000))
            ]);
            let widevine;
            try {
                await navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
                    initDataTypes:['cenc'], audioCapabilities:[{contentType:'audio/mp4; codecs="mp4a.40.2"'}]
                }]);
                widevine = {supported:true};
            } catch(error) { widevine = {supported:false, error:error.name}; }
            return {
                widevine, webdriver:navigator.webdriver, frame_clock_ms:frameClock,
                visibility:document.visibilityState, focused:document.hasFocus(),
                visible_dialogs:[...document.querySelectorAll('[role="dialog"],[role="alert"]')].filter(visible).map(n=>n.innerText.slice(0,300)),
                login_controls:[...document.querySelectorAll('button,a')].filter(n=>visible(n) && /^(log in|sign up)$/i.test(n.innerText.trim())).map(n=>n.innerText.trim()),
                audio:[...document.querySelectorAll('audio')].map(n=>({paused:n.paused,readyState:n.readyState,networkState:n.networkState,error:n.error?.code||null})),
                play_controls:[...document.querySelectorAll('[data-testid="control-button-playpause"]')].map(n=>({label:n.getAttribute('aria-label'),disabled:n.disabled})),
                progress:document.querySelector('[data-testid="playback-position"]')?.innerText,
                exact_rows:[...document.querySelectorAll('[data-testid="tracklist-row"]')].filter(n=>n.innerText.includes('Aaya Sher')).slice(0,2).map(n=>n.innerText.slice(0,250))
            };
        }""")
        browser._worker.run(
            lambda: browser._require_page().screenshot(path=str(screenshot_path))
        )
        result.update(
            browser="brave",
            config=browser_config("brave"),
            console_categories=categories,
            ignored_default_args=ignored_args,
        )
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True))
    finally:
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--screenshot", required=True, type=Path)
    parser.add_argument("--ignore-default-arg", action="append", default=[])
    arguments = parser.parse_args()
    inspect(arguments.report, arguments.screenshot, arguments.ignore_default_arg)
