// pm2 process definition for the GlobusOne MCP server.
//
// This exists because passthrough auth removed the reason it could not be a
// managed service. Under the old service-account mode the server needed a
// borrowed JSESSIONID pasted in at startup, so it had to be launched by hand
// every time the cookie expired — which is why it kept being the stale component
// while the backend ran happily under pm2.
//
// In passthrough mode there is no cookie and no startup token. Each request
// carries the signed-in user's own Okta token in its Authorization header, which
// the server reads per call. So it can simply run.
//
//   pm2 start ecosystem.config.js
//   pm2 save                        # survive a reboot
//   pm2 logs widget-cgpt-mcp
//
// Deliberately no GLOBUS_SESSION_COOKIE, GLOBUS_USER_ID, GLOBUS_PARTY_ID or
// GLOBUS_ACCESS_LEVEL: those are the service-account identity. Setting them here
// would silently re-enable the shared-session behaviour this replaced.

module.exports = {
  apps: [
    {
      name: "widget-cgpt-mcp",

      // pm2 resolves `script` as a file path and never hands it to the
      // interpreter, so the interpreter itself is the script here and
      // `interpreter: "none"` stops pm2 running it under node.
      //
      // `-m` rather than a path to server.py: the module form puts cwd on
      // sys.path and runs it as part of the package, which its imports expect.
      script: "/opt/llm/corporate_gpt_gadk/.venv/bin/python",
      args: "-m globus_mcp_server.server",
      interpreter: "none",
      cwd: "/opt/llm/globus_mcp_server",

      // The tool functions hold no per-request state, but the MCP session
      // manager does — a second worker would hand clients a session the other
      // process does not know about.
      instances: 1,
      exec_mode: "fork",

      autorestart: true,
      max_restarts: 10,
      // A crash loop should be visible rather than silently retried forever.
      min_uptime: "30s",
      restart_delay: 5000,

      env: {
        PYTHONPATH: ".",
        PYTHONUNBUFFERED: "1",

        GLOBUS_DATA_MODE: "live",
        GLOBUS_API_BASE: "https://erptest3.globusone.com/quote/api/",

        // Per-user token passthrough. Tokens are verified against Okta's JWKS on
        // every call; nothing is held between requests.
        GLOBUS_AUTH_MODE: "passthrough",
        GLOBUS_OKTA_ISSUER: "https://globusmedical.oktapreview.com/oauth2/default",
        GLOBUS_TOKEN_AUDIENCE: "api://default",

        // Must match GLOBUS_MCP_URL in the backend's .env, and what verify_live
        // reads. The config default is 8812; this deployment has always used 8813.
        GLOBUS_MCP_PORT: "8813",
      },
    },
  ],
};
