# Deploying the hosted server (Google Cloud Run)

The hosted server is a convenience for the hackathon. Teams can always run it locally with `uvx` or pip (see the [README](../README.md)), so nothing depends on this being up.

*Written September 20, 2026. Commands assume the `gcloud` CLI is installed and logged in.*

## What gets deployed

- **Streamable HTTP MCP** at `/mcp`, stateless, so instances are interchangeable.
- **`/health`** (open, no key) reports the snapshot version and whether any layer is missing.
- **A shared secret** is required on every `/mcp` request: `Authorization: Bearer <key>`. The server refuses to start on a public address without one.
- **The data snapshot downloads at startup** from this repository's GitHub release (about 6 MB, a second or two) and is verified against `snapshot.lock.json`.

Cloud Run builds from source with buildpacks (no Dockerfile). `Procfile` gives the start command, and `requirements.txt` tells the Python buildpack to install this project.

## One-time setup

```sh
PROJECT=<your-gcp-project>
REGION=us-west2                      # Los Angeles, closest to Glendale
SERVICE=glendale-gis-mcp

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

Create the shared secret and let the service read it:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | \
  gcloud secrets create glendale-gis-api-key --data-file=-

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding glendale-gis-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

Print the key when you need to hand it to teams: `gcloud secrets versions access latest --secret=glendale-gis-api-key`.

## Deploy

```sh
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-secrets "GLENDALE_GIS_API_KEYS=glendale-gis-api-key:latest" \
  --set-env-vars "GLENDALE_GIS_HTTP_HOST=0.0.0.0,GLENDALE_GIS_CONTACT=ryan@hacker.fund" \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 40 \
  --max-instances 4 \
  --min-instances 0 \
  --timeout 120
```

- **`--allow-unauthenticated`** lets requests reach the service; our own shared secret is the access control. Google's IAM auth would need every participant to hold a Google identity and mint tokens, which MCP clients can't do.
- **`--memory 1Gi`:** the snapshot needs roughly 300–400 MB in memory, mostly the dam inundation layer.
- **`--max-instances 4`** caps the cost if the key leaks or a client misbehaves.
- **`--timeout 120`** is plenty; requests answer in milliseconds from the snapshot.

After deploying, lock the `Host` header to the service's own hostname (this is DNS-rebinding protection, and it can only be set once you know the URL):

```sh
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "GLENDALE_GIS_HTTP_ALLOWED_HOSTS=${URL#https://}"
echo "$URL"
```

**During the event**, avoid cold starts:

```sh
gcloud run services update "$SERVICE" --region "$REGION" --min-instances 1
```

Set it back to 0 afterwards. A warm instance costs roughly a few dollars a day.

## Check it

```sh
curl -s "$URL/health"                     # open; expect "status": "ok"
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL/mcp" -H 'content-type: application/json' -d '{}'   # expect 401
```

Then connect a client. In Claude Code:

```sh
claude mcp add --transport http glendale-gis "$URL/mcp" --header "Authorization: Bearer <key>"
```

Claude Desktop can't send custom headers on a remote MCP server, so teams using Desktop should install locally with `uvx` (see the README).

## Settings worth knowing

| Variable | Default | Purpose |
| --- | --- | --- |
| `GLENDALE_GIS_API_KEYS` | none | Comma-separated shared secrets. Required on a public address. Two values let you rotate with an overlap. |
| `GLENDALE_GIS_HTTP_HOST` | `127.0.0.1` | Must be `0.0.0.0` on Cloud Run |
| `PORT` | 8000 | Set by Cloud Run; the server honors it |
| `GLENDALE_GIS_HTTP_ALLOWED_HOSTS` | none | Allowed `Host` headers. Empty turns the check off |
| `GLENDALE_GIS_HTTP_ALLOWED_ORIGINS` | none | Allowed browser `Origin` headers |
| `GLENDALE_GIS_RATE_LIMIT_PER_MINUTE` | 120 | Per key, or per client IP. Counted per instance |
| `GLENDALE_GIS_MAX_REQUEST_BYTES` | 4 MB | Larger requests get 413 |
| `GLENDALE_GIS_CONTACT` | `ryan@hacker.fund` | Goes in the User-Agent sent to the agency servers |
| `GLENDALE_GIS_SNAPSHOT_PATH` | unset | Use a local snapshot instead of downloading |

## Rotating the key

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | \
  gcloud secrets versions add glendale-gis-api-key --data-file=-
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-secrets "GLENDALE_GIS_API_KEYS=glendale-gis-api-key:latest"
```

To keep the old key working during a changeover, set `GLENDALE_GIS_API_KEYS` to both values for a while, then drop the old one.

## Publishing fresh data

Rebuild and publish the snapshot, then restart the service so it picks up the new release:

```sh
python scripts/build_snapshot.py --publish     # then commit snapshot.lock.json
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "REDEPLOYED_AT=$(date -u +%FT%TZ)"
```

## Shutting down after the event

```sh
gcloud run services update "$SERVICE" --region "$REGION" --min-instances 0
gcloud run services delete "$SERVICE" --region "$REGION"     # when you're done entirely
gcloud secrets delete glendale-gis-api-key
```

## Troubleshooting

- **The server won't start, and the logs say "Refusing to listen on 0.0.0.0 without an API key":** the secret isn't reaching the service. Check `--set-secrets` and the `secretAccessor` binding.
- **Every request returns 421:** the `Host` header doesn't match `GLENDALE_GIS_HTTP_ALLOWED_HOSTS`. Set it to the service hostname without `https://`, or unset it.
- **`/health` says `"status": "degraded"`:** read `detail` and `layers_unavailable`. The snapshot download may have failed, in which case the server may be serving older data with results marked `stale`.
- **Logs:** `gcloud run services logs read "$SERVICE" --region "$REGION" --limit 50`. Keys and addresses are never logged.
