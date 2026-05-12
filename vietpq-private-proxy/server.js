const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();
const PORT = process.env.PORT || 18111;

app.use((req, res, next) => {
  const origin = req.headers.origin || "*";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-Requested-With");
  if (req.method === "OPTIONS") return res.sendStatus(204);
  next();
});

// API -> backend
app.use(createProxyMiddleware({
  target: "http://127.0.0.1:8111",
  changeOrigin: true,
  pathFilter: (path) => path.startsWith("/vietpq-scraper/api"),
  pathRewrite: { "^/vietpq-scraper/api": "" },
}));

// Frontend -> keep nguyên path /vietpq-scraper/*
app.use(createProxyMiddleware({
  target: "http://127.0.0.1:3111",
  changeOrigin: true,
  ws: true,
  pathFilter: (path) =>
    path.startsWith("/vietpq-scraper") &&
    !path.startsWith("/vietpq-scraper/api"),
}));

app.listen(PORT, "0.0.0.0", () => {
  console.log(`vietpq-private-proxy listening on :${PORT}`);
});
