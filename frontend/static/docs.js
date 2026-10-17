"use strict";

// The same local help page must show usable commands on localhost and public HTTPS.
const apiOrigin = window.location.origin;
document.getElementById("api-origin").textContent = apiOrigin;
for (const block of document.querySelectorAll("pre")) {
  block.textContent = block.textContent.replaceAll(
    "http://127.0.0.1:8000",
    apiOrigin,
  );
}
