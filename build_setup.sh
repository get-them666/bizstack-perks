#!/bin/bash
echo "📦 Initializing production data directory blocks..."
mkdir -p data
if [ ! -f data/perks.json ]; then
  echo "[]" > data/perks.json
  echo "✅ Placeholder perks.json matrix created."
fi
