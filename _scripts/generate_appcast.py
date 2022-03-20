#!/usr/bin/python3
"""
A script that generates an appcast from GitHub releases.
"""

import os
import shutil
import plistlib
import subprocess

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


def generate():
    try:

        resp = requests.get(GITHUB_RELEASES_API)
        releases = resp.json()

        appcast_items = []
        appcast_pre_items = []

        # Iterate over the last 5 releases and collect data for the appcast.
        # TODO: Add only new releases to app-cast.

        for r in releases[:5]:

            # Get short version
            release_name = r["name"]

            print(f"Processing release {release_name}...")

            # Get release notes
            release_notes = r["body"]  # This is markdown

            # Write release notes to file.
            os.makedirs(DOWNLOAD_FOLDER_ABSOLUTE, exist_ok=True)

            with open(f"{DOWNLOAD_FOLDER}/release_notes.md", "w") as f:
                f.write(release_notes)

            # Convert to HTML
            release_notes = subprocess.check_output(
                f"cat {DOWNLOAD_FOLDER}/release_notes.md | pandoc -f markdown -t html -c '' -H {RELEASE_NOTES_CSS} -s --metadata title='{release_name}'",
                stderr=subprocess.STDOUT,
                shell=True,
            ).decode()

            # Get title
            title = f"{release_name} available"

            # Get publishing date
            publising_date = r["published_at"]

            # Get isPrerelease
            is_prerelease = r["prerelease"]

            # Get download link
            download_link = r["assets"][0]["browser_download_url"]

            # Download dmg
            os.makedirs(DOWNLOAD_FOLDER_ABSOLUTE, exist_ok=True)
            download_name = download_link.rsplit("/", 1)[-1]
            download_path = f"{DOWNLOAD_FOLDER}/{download_name}"

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
                # skip all pre-sparkle app bundles
                continue

            # Get edSignature
            cmd = f"./{SPARKLE_SIGN_PATH} {download_path}"

            if SPARKLE_PRIVATE_KEY:
                cmd += f" -s {SPARKLE_PRIVATE_KEY}"

            signature_and_length = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode()
            signature_and_length = signature_and_length[0:-1]

            # Assemble collected data into appcast.xml-ready item-string
            item_string = f"""\
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
            type="application/octet-stream"
        />
    </item>"""

            # Append item_string to arrays
            if not is_prerelease:
                appcast_items.append(item_string)

            appcast_pre_items.append(item_string)

        # Clean up downloaded files
        shutil.rmtree(DOWNLOAD_FOLDER, ignore_errors=True)

        # Assemble item strings into final appcast strings

        items_joined = "\n".join(appcast_items)
        pre_items_joined = "\n".join(appcast_pre_items)

        appcast_content_string = f"""\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>{APP_NAME} Update Feed</title>
    <link>{APPCAST_URL}</link>
    <description>Stable releases of {APP_NAME}</description>
    <language>en</language>
    {items_joined}
  </channel>
</rss>"""

        appcast_pre_content_string = f"""\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>{APP_NAME} Update Feed for Prereleases</title>
    <link>{APPCAST_PRE_URL}</link>
    <description>Prereleases of {APP_NAME}</description>
    <language>en</language>
    {pre_items_joined}
  </channel>
</rss>"""

        # Write to file

        with open(APPCAST_FILENAME, "w") as f:
            f.write(appcast_content_string)

        with open(APPCAST_PRE_FILENAME, "w") as f:
            f.write(appcast_pre_content_string)

    finally:
        # Clean up download folder
        shutil.rmtree(DOWNLOAD_FOLDER, ignore_errors=True)


if __name__ == "__main__":
    generate()
