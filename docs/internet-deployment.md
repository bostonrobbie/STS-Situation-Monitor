# Internet deployment guide

This project can be exposed publicly with TLS using the provided Caddy reverse-proxy compose file.

## 1) Configure environment

Add/update these in `.env`:

- `STS_PUBLIC_BASE_URL=https://your-domain.example`
- `STS_TRUSTED_HOSTS=your-domain.example`
- `STS_CORS_ORIGINS=https://your-frontend.example`
- `STS_ALERT_WEBHOOK_URL=` (optional Slack/Discord/custom webhook)
- `STS_ALERT_WEBHOOK_TIMEOUT_S=5`

## 2) Point DNS

Create an `A`/`AAAA` record for your server:

- `your-domain.example -> <your-public-ip>`

## 3) Start public stack

```bash
docker compose -f docker-compose.public.yml up -d --build
```

Caddy will automatically provision TLS certificates.

## 4) Validate internet exposure

```bash
curl -i https://your-domain.example/health
curl -H "X-API-Key: ..." https://your-domain.example/system/online-tools
```

## 5) Optional online alert delivery

Set `STS_ALERT_WEBHOOK_URL` to a webhook receiver. Trigger an alert with `/alerts/evaluate/{id}` and confirm the receiver gets JSON payloads.

## Security checklist

- Keep `STS_ENFORCE_AUTH=true`
- Use a strong `STS_AUTH_API_KEY`
- Restrict `STS_TRUSTED_HOSTS` and `STS_CORS_ORIGINS` to exact domains
- Place infrastructure firewall rules in front of the host
- Rotate API keys periodically
