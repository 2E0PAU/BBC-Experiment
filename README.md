# BBC Springwatch Wildlife Cam Wall

A small Flask web app that discovers the current Springwatch wildlife-camera webcasts from the public BBC live page and shows them as a local windowed cam wall.

There does not appear to be a documented public BBC API specifically for the Springwatch wildlife webcams. The app uses the public Springwatch live page as the source of truth, then requests BBC media-selector playback URLs at runtime. It does not store stream URLs, download content, or bypass BBC availability checks.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

Then open <http://127.0.0.1:5000>.

## Notes

- Springwatch 2026 is scheduled on the BBC live page from 25 May to 11 June.
- The wildlife cameras are listed there as live from 10:00 to 22:00 Monday to Thursday, and 10:00 to 18:00 Friday to Sunday.
- Playback may fail outside the live window, outside permitted BBC territories, or if the BBC changes the page/player data format.

## Tests

```powershell
.\.venv\Scripts\python -m unittest discover -s tests
```
