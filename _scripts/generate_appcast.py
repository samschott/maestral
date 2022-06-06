#!/usr/bin/python3
"""
A script that generates an appcast from GitHub releases.
"""

import os
import shlex
import plistlib
import tempfile
import subprocess
import textwrap

import requests

APP_NAME = "Maestral"

GITHUB_RELEASES_API = "https://api.github.com/repos/SamSchott/maestral/releases"
RAW_APPCAST_URL = "https://maestral.app/"

APPCAST_FILENAME = "appcast.xml"
APPCAST_PRE_FILENAME = "appcast-pre.xml"

# URL of appcasts
APPCAST_URL = f"{RAW_APPCAST_URL}/{APPCAST_FILENAME}"  # stable
APPCAST_PRE_URL = f"{RAW_APPCAST_URL}/{APPCAST_PRE_FILENAME}"  # prerelease

# Path of Sparkle code signing tool
SPARKLE_SIGN_PATH = "_scripts/sparkle_sign_update"

RELEASE_NOTES_CSS = "_scripts/release_notes.css"

# Temp folder to download dmgs and release notes. Delete this on exit.
DOWNLOAD_FOLDER = "generate_appcasts_downloads"
DOWNLOAD_FOLDER_ABSOLUTE = os.path.join(os.getcwd(), DOWNLOAD_FOLDER)

SPARKLE_PRIVATE_KEY = os.environ.get("SPARKLE_PRIVATE_KEY")

APPCAST_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>{app_name} Update Feed</title>
    <link>{appcast_url}</link>
    <description>Stable releases of {app_name}</description>
    <language>en</language>
{items_joined}
  </channel>
</rss>"""

APPCAST_XML_PRE = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>{app_name} Update Feed for Prereleases</title>
    <link>{appcast_url}</link>
    <description>Prereleases of {app_name}</description>
    <language>en</language>
{items_joined}
  </channel>
</rss>"""

APPCAST_ITEM_XML = """\
<item>
  <title>{title}</title>
  <pubDate>{publising_date}</pubDate>
  <sparkle:version>{build_number}</sparkle:version>
  <sparkle:shortVersionString>{short_version_str}</sparkle:shortVersionString>
  <sparkle:minimumSystemVersion>{min_system}</sparkle:minimumSystemVersion>
  <description><![CDATA[
{release_notes}
  ]]>
  </description>
  <enclosure
    url="{download_link}"
    {signature_and_length}
    type="application/octet-stream"/>
</item>"""


def generate():
    resp = requests.get(GITHUB_RELEASES_API)
    releases = resp.json()

    appcast_items = []
    appcast_pre_items = []

    # Iterate over the last 5 releases and collect data for the appcast.
    # TODO: Add only new releases to app-cast.

    for r in releases[:5]:

        # Get short version.
        release_name = r["name"]

        print(f"Processing release {release_name}...")

        # Get release notes.
        release_notes = r["body"]  # This is markdown

        # Write release notes to file.
        os.makedirs(DOWNLOAD_FOLDER_ABSOLUTE, exist_ok=True)

        with open(f"{DOWNLOAD_FOLDER}/release_notes.md", "w") as f:
            f.write(release_notes)

        # Convert to HTML.
        release_notes = subprocess.check_output(
            f"cat {DOWNLOAD_FOLDER}/release_notes.md | pandoc -f markdown -t html -c '' -H {RELEASE_NOTES_CSS} -s --metadata title='{release_name}'",
            stderr=subprocess.STDOUT,
            shell=True,
        ).decode()

        # Get title.
        title = f"{release_name} available"

        # Get publishing date.
        publising_date = r["published_at"]

        # Get isPrerelease.
        is_prerelease = r["prerelease"]

        # Get download link.
        download_link = r["assets"][0]["browser_download_url"]

        with tempfile.TemporaryDirectory() as tmpdir:
            # Download dmg
            download_name = download_link.rsplit("/", 1)[-1]
            download_path = f"{tmpdir}/{download_name}"

            res = requests.get(download_link, allow_redirects=True)

            with open(download_path, "wb") as f:
                f.write(res.content)

            # Get build number and minimum system version from Info.plist
            res = subprocess.check_output(["hdiutil", "attach", download_path]).decode()
            mount_point = res.split("\t")[-1].strip()

            with open(f"{mount_point}/{APP_NAME}.app/Contents/Info.plist", "rb") as f:
                info = plistlib.load(f)

            subprocess.check_output(["hdiutil", "detach", "-force", mount_point])

            short_version_str = info["CFBundleShortVersionString"]
            build_number = info["CFBundleVersion"]
            min_system = info["LSMinimumSystemVersion"]

            if "SUPublicEDKey" not in info:
                print(f"Skipping {release_name}, no SUPublicEDKey in Info.plist")
                continue

            # Get edSignature
            cmd = f"./{SPARKLE_SIGN_PATH} {shlex.quote(download_path)}"

            if SPARKLE_PRIVATE_KEY:
                cmd += f" -s {SPARKLE_PRIVATE_KEY}"

            signature_and_length = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.STDOUT
            ).decode()
            signature_and_length = signature_and_length.strip("\n")

        # Assemble collected data into appcast.xml-ready item-string.
        item_string = APPCAST_ITEM_XML.format(
            title=title,
            publising_date=publising_date,
            build_number=build_number,
            short_version_str=short_version_str,
            min_system=min_system,
            release_notes=textwrap.indent(release_notes, 4 * " "),
            download_link=download_link,
            signature_and_length=signature_and_length,
        )

        # Append item_string to arrays.
        appcast_pre_items.append(item_string)

        if not is_prerelease:
            appcast_items.append(item_string)

    # Assemble final appcast xml.
    items_joined = "\n".join(appcast_items)
    pre_items_joined = "\n".join(appcast_pre_items)

    appcast_content_string = APPCAST_XML.format(
        app_name=APP_NAME,
        appcast_url=APPCAST_URL,
        items_joined=textwrap.indent(items_joined, prefix=4 * " "),
    )
    appcast_pre_content_string = APPCAST_XML_PRE.format(
        app_name=APP_NAME,
        appcast_url=APPCAST_PRE_URL,
        items_joined=textwrap.indent(pre_items_joined, prefix=4 * " "),
    )

    # Write to file.
    with open(APPCAST_FILENAME, "w") as f:
        f.write(appcast_content_string)

    with open(APPCAST_PRE_FILENAME, "w") as f:
        f.write(appcast_pre_content_string)


if __name__ == "__main__":
    generate()
