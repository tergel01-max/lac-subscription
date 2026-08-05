#!/usr/bin/env python3
"""
Automate the "paste a sentence into OpenAI and get a better Mongolian
translation" workflow across two whole Google Docs.

Given an English source Google Doc and its existing Mongolian translation,
this script asks the OpenAI API to align the two and produce an improved
Mongolian translation for the entire document, then saves the result
(and can upload it back to Drive as a new Google Doc).

Usage:
    python translate_improve.py \
        --english   <google-doc-id-or-url> \
        --mongolian <google-doc-id-or-url> \
        --out        improved_mn.txt \
        [--upload]           # also create a new Google Doc with the result
        [--model gpt-4o]     # or set OPENAI_MODEL

Setup instructions are in README.md.
"""

import argparse
import os
import re
import sys
import textwrap

from openai import OpenAI

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# Read to export docs; drive.file to create the improved copy back in Drive.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# If both docs together are shorter than this many characters we send them in
# a single request (best alignment). Above it we fall back to chunking the
# Mongolian doc while keeping the full English as reference context.
SINGLE_CALL_CHAR_LIMIT = 40_000

SYSTEM_PROMPT = textwrap.dedent(
    """
    You are an expert bilingual editor specializing in English-to-Mongolian
    translation. You will be given an English SOURCE text and an existing
    MONGOLIAN translation of it.

    Your job: produce an improved Mongolian translation.

    Requirements:
    - Align the Mongolian to the English meaning; fix mistranslations,
      awkward phrasing, and unnatural constructions.
    - Make it read as fluent, natural, professional Mongolian a native
      speaker would write — not literal word-for-word translation.
    - Preserve the meaning, tone, and any domain/terminology from the source.
    - Keep the same paragraph structure and the same number of paragraphs
      as the Mongolian input (one improved paragraph per input paragraph).
    - Preserve numbers, names, URLs, and any markup/placeholders exactly.

    Output ONLY the improved Mongolian text. Do not add explanations,
    comments, headings, or the English text.
    """
).strip()


# --------------------------------------------------------------------------- #
# Google Drive helpers
# --------------------------------------------------------------------------- #
def extract_doc_id(value: str) -> str:
    """Accept a raw Google Doc ID or a full Drive/Docs URL."""
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", value)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", value)
    if m:
        return m.group(1)
    return value.strip()


def get_drive_service():
    """OAuth installed-app flow. Caches token.json after first consent."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                sys.exit(
                    "Missing credentials.json — download an OAuth client "
                    "(Desktop app) from Google Cloud Console. See README.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def export_google_doc_text(service, doc_id: str) -> str:
    """Export a Google Doc as plain text."""
    data = service.files().export(fileId=doc_id, mimeType="text/plain").execute()
    return data.decode("utf-8") if isinstance(data, bytes) else data


def upload_as_google_doc(service, text: str, name: str) -> str:
    """Create a new Google Doc from plain text; returns its URL."""
    media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
    file = (
        service.files()
        .create(
            body={"name": name, "mimeType": "application/vnd.google-apps.document"},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    return f"https://docs.google.com/document/d/{file['id']}/edit"


# --------------------------------------------------------------------------- #
# OpenAI helpers
# --------------------------------------------------------------------------- #
def split_paragraphs(text: str):
    """Split on blank lines, keeping non-empty paragraphs."""
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def improve(client: OpenAI, model: str, english: str, mongolian: str) -> str:
    user_prompt = (
        f"ENGLISH SOURCE:\n{english}\n\n"
        f"MONGOLIAN TRANSLATION (to improve):\n{mongolian}"
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


def improve_document(client: OpenAI, model: str, english: str, mongolian: str) -> str:
    """Single call for small docs; chunk the Mongolian for large ones."""
    if len(english) + len(mongolian) <= SINGLE_CALL_CHAR_LIMIT:
        return improve(client, model, english, mongolian)

    print("Document is large — processing in chunks with full English context...")
    mn_paras = split_paragraphs(mongolian)
    chunk, chunks, size = [], [], 0
    for para in mn_paras:
        if size + len(para) > 8000 and chunk:
            chunks.append("\n\n".join(chunk))
            chunk, size = [], 0
        chunk.append(para)
        size += len(para)
    if chunk:
        chunks.append("\n\n".join(chunk))

    out = []
    for i, mn_chunk in enumerate(chunks, 1):
        print(f"  chunk {i}/{len(chunks)}...")
        out.append(improve(client, model, english, mn_chunk))
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Improve a Mongolian translation with OpenAI.")
    ap.add_argument("--english", required=True, help="English source Google Doc ID or URL")
    ap.add_argument("--mongolian", required=True, help="Mongolian translation Google Doc ID or URL")
    ap.add_argument("--out", default="improved_mn.txt", help="Local output file")
    ap.add_argument("--upload", action="store_true", help="Also create a new Google Doc with the result")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o"), help="OpenAI model")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY (see README.md).")

    client = OpenAI()
    drive = get_drive_service()

    print("Exporting Google Docs...")
    english = export_google_doc_text(drive, extract_doc_id(args.english))
    mongolian = export_google_doc_text(drive, extract_doc_id(args.mongolian))

    print(f"Improving translation with {args.model}...")
    improved = improve_document(client, args.model, english, mongolian)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(improved)
    print(f"Saved improved Mongolian translation -> {args.out}")

    if args.upload:
        url = upload_as_google_doc(drive, improved, "Improved Mongolian Translation")
        print(f"Uploaded to Google Drive -> {url}")


if __name__ == "__main__":
    main()
