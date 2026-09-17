# JoblyGo web app

React + TypeScript (Vite). Signs in with Cognito's hosted pages
(authorization code + PKCE, no client secret) and talks to the API at
`VITE_API_BASE` with the Cognito **ID token**.

    cp .env.example .env.local     # the deployed pool and API
    npm install
    npm run dev                    # http://localhost:5173 (an allowed sign-in redirect)
    npm test                       # unit tests (vitest)
    npm run build                  # → dist/, uploaded to S3 by .github/workflows/cloud.yml

Tokens: the ID and access tokens stay in memory; the refresh token is kept in
`sessionStorage`, so closing the tab signs you out. `src/auth.ts` holds the
whole flow and is unit-tested without a browser.
