# abb-reveal

AudiobookBay hides some search results as base64 inside `<div class="post re-ab" style="display:none;">`. The decoded text is the post itself: title, size and link. Prowlarr's Cardigann definitions only see the HTML that was downloaded, so those rows never reach Listenarr, Readarr, or any other Torznab client.

This proxy fetches the site, decodes those text-only blocks in place, and rewrites absolute AudiobookBay links to root-relative paths. The outer `div.post` stays, because that is what the row selector matches. Detail pages that are already ordinary HTML pass through, including the info hash.

## Run

Prowlarr has to reach the proxy by the host name in the definition. Do not publish this port on the public internet. Anyone who can open it can ask it to fetch the configured upstream.

```bash
docker compose up -d
docker network connect abb-reveal <prowlarr-container>
```

`UPSTREAM` defaults to `https://audiobookbay.lu`. Change it if the site moves. Redirects to any other host are refused.

The process runs as uid 65534 and the script is mounted read-only, so `reveal.py` must be readable by other users. If the container logs `Permission denied` on a NAS share, that share's ACL is overriding the file mode. Remove the extra ACL, or run the service as root.

You can instead add the service to the Compose project that already runs Prowlarr and attach it to that project's network. The definition link has to be a name Prowlarr can resolve.

## Prowlarr

1. Copy `prowlarr/audiobook-bay.yml` into Prowlarr's `Definitions/Custom/` directory.
2. Keep `links` on the proxy URL. The file ships with `http://abb-reveal:8080/`.
3. Set the indexer's saved base URL to that same link. Editing the definition file does not change a base URL that was already saved.
4. Use the audiobook category and tags you already use. This repository does not choose them.

The definition sends a browser user agent, because the site rejects Prowlarr's own agent. Search words are lowercased, because a query that contains capitals is redirected away from the results. Sizes look like `1.18 GBs`. The trailing `s` stays outside the capture group, or the size is parsed as zero.

## Check

`GET /__abb_reveal_health` returns `ok`.

A search page that mixes normal posts with hidden ones returns `X-Abb-Revealed` set to how many blocks were decoded. Titles that were missing should appear as normal rows with a non-zero size. A grab should still resolve the info hash from the book page.

```bash
python3 -m unittest test_reveal.py
```

## What it does not do

It does not log into the site, store cookies, or forward a request to any host other than `UPSTREAM`. It does not talk to a torrent client.
