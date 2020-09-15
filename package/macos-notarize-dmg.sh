#!/usr/bin/env bash

if [ -z "$1" ]; then
    echo "Specify dmg as first parameter"
    exit 1
fi

if [ -z "$APPLE_ID_USER" ] || [ -z "$APPLE_ID_PASSWORD" ]; then
    echo "You need to set your Apple ID credentials with \$APPLE_ID_USER and \$APPLE_ID_PASSWORD."
    exit 1
fi

APP_BUNDLE=$(basename "$1")
APP_BUNDLE_DIR=$(dirname "$1")

cd "$APP_BUNDLE_DIR" || exit 1

# Submit for notarization
TMPFILE=$(mktemp)

echo "Submitting $APP_BUNDLE for notarization..."
xcrun altool --notarize-app --type osx \
  --file "${APP_BUNDLE}" \
  --primary-bundle-id com.samschott.maestral \
  --username $APPLE_ID_USER \
  --password @env:APPLE_ID_PASSWORD \
  --output-format xml > $TMPFILE

REQUEST_UUID=$(/usr/libexec/PlistBuddy -c "Print notarization-upload:RequestUUID" "$TMPFILE")

if [ -z "$REQUEST_UUID" ]; then
  echo "Submitting $APP_BUNDLE failed:"
  echo "$RESULT"
  exit 1
fi

# Poll for notarization status
echo "Submitted notarization request $REQUEST_UUID, waiting for response..."
sleep 60
while :
do
  xcrun altool --notarization-info "$REQUEST_UUID" \
    --username "$APPLE_ID_USER" \
    --password @env:APPLE_ID_PASSWORD \
    --output-format xml > $TMPFILE

  STATUS=$(/usr/libexec/PlistBuddy -c "Print notarization-info:Status" "$TMPFILE")

  if [ "$STATUS" = "success" ]; then
    echo "Notarization of $APP_BUNDLE succeeded!"
    break
  elif [ "$STATUS" = "in progress" ]; then
    echo "Notarization in progress..."
    sleep 20
  else
    echo "Notarization of $APP_BUNDLE failed:"
    echo "$RESULT"
    exit 1
  fi
done

# Staple the notary ticket
xcrun stapler staple "$APP_BUNDLE"
