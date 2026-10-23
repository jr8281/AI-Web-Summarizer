import os
import json
import sys

# Add parent directory to path so we can use the shared .env and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from scraper import fetch_website_links, fetch_website_contents

# ── Load env ────────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

# ── Model – DO NOT change this; the user controls which model is active ──────
MODEL = 'openai/gpt-oss-120b'

groq_client = Groq()

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Web Summarizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend static files
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


# ── Prompts ───────────────────────────────────────────────────────────────────
LINK_SYSTEM_PROMPT = """
You are provided with a list of links found on a webpage.
You are able to decide which of the links would be most relevant to include in a brochure about the company,
such as links to an About page, or a Company page, or Careers/Jobs pages.
You should respond in JSON as in this example:

{
    "links": [
        {"type": "about page", "url": "https://full.url/goes/here/about"},
        {"type": "careers page", "url": "https://another.full.url/careers"}
    ]
}
"""

SUMMARY_SYSTEM_PROMPT = """
You are an assistant that analyzes the contents of several relevant pages from a company website
and creates a short brochure-style summary about the company for prospective customers, investors and recruits.
Respond in markdown. Include details of company culture, customers and careers/jobs if you have the information.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_links_user_prompt(url: str) -> str:
    user_prompt = f"""
Here is the list of links on the website {url} -
Please decide which of these are relevant web links for a brochure about the company,
respond with the full https URL in JSON format.
Do not include Terms of Service, Privacy, email links.

Links (some might be relative links):

"""
    links = fetch_website_links(url)
    user_prompt += "\n".join(links)
    return user_prompt


def select_relevant_links(url: str) -> dict:
    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": LINK_SYSTEM_PROMPT},
            {"role": "user", "content": get_links_user_prompt(url)},
        ],
        response_format={"type": "json_object"},
    )
    result = response.choices[0].message.content
    return json.loads(result)


def fetch_page_and_all_relevant_links(url: str) -> str:
    contents = fetch_website_contents(url)
    relevant_links = select_relevant_links(url)
    result = f"## Landing Page:\n\n{contents}\n## Relevant Links:\n"
    for link in relevant_links.get("links", []):
        result += f"\n\n### Link: {link['type']}\n"
        result += fetch_website_contents(link["url"])
    return result


def get_brochure_user_prompt(company_name: str, url: str) -> str:
    user_prompt = f"You are looking at a company called: {company_name}\n"
    user_prompt += f"Here are the contents of its landing page and other relevant pages; use this information to build a short brochure of the company in markdown.\n"
    user_prompt += fetch_page_and_all_relevant_links(url)
    return user_prompt[:5_000]  # Truncate to avoid token limits


# ── API endpoints ─────────────────────────────────────────────────────────────
class SummarizeRequest(BaseModel):
    url: str
    company_name: str = ""


@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(frontend_dir, "index.html"))


@app.post("/summarize")
async def summarize(request: SummarizeRequest):
    url = request.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    company_name = request.company_name.strip() or url

    try:
        user_prompt = get_brochure_user_prompt(company_name, url)

        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        summary = response.choices[0].message.content
        return {"summary": summary, "model": MODEL}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}
