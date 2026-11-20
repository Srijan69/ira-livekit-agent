"""Explicit real-host evaluation through IRA's scoped Spotify voice tool.

Run from the repository root with uv run python scripts/evaluate_spotify_playback.py
--track "Aaya Sher" --artist "Anirudh Ravichander" --report <path>.
This plays the requested song briefly and closes the evaluation browser afterwards.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from browser import BrowserController
from personal_memory import PersonalMemoryStore


async def evaluate(track, artist, diagnostics=False):
    browser = BrowserController()
    click_trace = []
    native_click = browser._native_click

    def watched_click(element, *, preflight=None):
        def state():
            return element.evaluate("""node => { const rect=node.getBoundingClientRect(), style=getComputedStyle(node);
            return {label:node.getAttribute('aria-label'),connected:node.isConnected,
            bounds:[rect.x,rect.y,rect.width,rect.height],display:style.display,visibility:style.visibility,
            opacity:style.opacity,disabled:node.disabled}; }""")

        element.evaluate("""node => { window.__iraClickTrace=[];
            for(const type of ['pointerover','pointerdown','pointerup','click'])
                node.addEventListener(type,event => window.__iraClickTrace.push({type,trusted:event.isTrusted}),{once:true});
        }""")
        click_trace.append({"phase": "before-trial", **state()})

        def checked():
            click_trace.append({"phase": "after-trial", **state()})
            if preflight is not None:
                preflight()

        return native_click(element, preflight=checked)

    if diagnostics:
        browser._native_click = watched_click
    memory = PersonalMemoryStore()
    agent = Assistant(browser=browser, personal_memory=memory)
    request = agent._request_scope.begin_request(
        f"Play {track} by {artist} on Spotify using the saved preferred browser",
        source_id="evaluation:explicit-command-line",
    )
    speech = SpeechHandle.create()
    agent._scoped_tools.bind_speech(speech, request)
    context = SimpleNamespace(
        speech_handle=speech,
        function_call=SimpleNamespace(call_id="spotify-playback-evaluation"),
    )
    tool = next(t for t in agent.tools if t.info.name == "spotify_play_track")
    started = time.monotonic()
    result = {"track": track, "artist": artist}

    def progress():
        return browser.evaluate("""() => ({
            position:document.querySelector('[data-testid="playback-position"]')?.innerText,
            duration:document.querySelector('[data-testid="playback-duration"]')?.innerText
        })""")

    try:
        result["tool_result"] = await tool(context, name=track, artist=artist)
        result["initial_player"] = await asyncio.to_thread(browser.spotify_player_state)
        result["progress_first"] = await asyncio.to_thread(progress)
        for _ in range(5):
            await asyncio.sleep(1)
            result["final_player"] = await asyncio.to_thread(
                browser.spotify_player_state
            )
            result["progress_second"] = await asyncio.to_thread(progress)
            if result["progress_second"].get("position") != result[
                "progress_first"
            ].get("position"):
                break
        result["status"] = (
            "verified"
            if result["final_player"].get("playing")
            and result["progress_second"].get("position")
            != result["progress_first"].get("position")
            else "not_sustained"
        )
    except Exception as exc:
        result.update(status="blocked", reason=str(exc))
        if browser.is_open:
            result["player"] = await asyncio.to_thread(browser.spotify_player_state)
            result["player_dom"] = await asyncio.to_thread(
                browser.evaluate,
                """() => { const bar=document.querySelector('[data-testid="now-playing-bar"]');
                return bar ? {text:bar.innerText.substring(0,600),
                links:[...bar.querySelectorAll('a')].map(a=>({text:a.innerText,href:a.getAttribute('href')})),
                titleNodes:[...bar.querySelectorAll('[title],[data-testid]')].map(e=>({tag:e.tagName,testid:e.getAttribute('data-testid'),title:e.getAttribute('title'),text:(e.innerText||'').substring(0,100)})).slice(0,20),
                alerts:[...document.querySelectorAll('[role="alert"],[role="dialog"]')].map(e=>(e.innerText||'').substring(0,300))} : null; }""",
            )
    finally:
        if diagnostics and browser.is_open:
            result["click_trace"] = click_trace
            result["pointer_events"] = await asyncio.to_thread(
                browser.evaluate, "() => window.__iraClickTrace || []"
            )
            screenshot = (
                Path(__file__).resolve().parents[1]
                / "dashboard"
                / "shots"
                / "spotify-click-diagnostics.png"
            )
            await asyncio.to_thread(
                browser._worker.run,
                lambda: browser._require_page().screenshot(path=str(screenshot)),
            )
        result["browser"] = browser._browser
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        await agent.on_exit()
        memory.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True)
    parser.add_argument("--artist", default="")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    output = asyncio.run(evaluate(args.track, args.artist, args.diagnostics))
    if args.report:
        args.report.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(output, ensure_ascii=True), flush=True)
    sys.exit(0 if output["status"] == "verified" else 1)
