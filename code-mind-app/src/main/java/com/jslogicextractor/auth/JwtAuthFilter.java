package com.jslogicextractor.auth;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.crypto.SecretKey;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Verifies the JWT StoryForge's FastAPI backend issues on login -- this app
 * never issues its own tokens, only checks the same shared secret (see
 * security.jwt-secret / JWT_SECRET in application.yml, which must match
 * StoryForge's backend/config.py JWT_SECRET). A single login covers both
 * apps this way.
 *
 * <p>The token can arrive as an {@code Authorization: Bearer} header (normal
 * requests, attached by the Angular interceptor), a {@code cm_token} cookie,
 * or a {@code ?token=} query param -- the latter exists because the Angular
 * shell's CodeMind iframe is cross-origin and can't share the shell's
 * localStorage, so its initial src URL carries the token as a query param
 * instead. Once that first request validates, a {@code cm_token} cookie is
 * minted so every *subsequent* same-origin request from that browser --
 * Thymeleaf's own topnav/job links, the "start a job" form POST, this app's
 * own polling fetch()/EventSource calls -- carries the token automatically,
 * without the query param having to be threaded through every link and
 * script by hand (it previously wasn't, which meant any page you navigated
 * to past the iframe's first load looked logged-out).
 *
 * <p>Registered (via SecurityFilterConfig, not a bean-scanned {@code
 * @Component}) only for /api/** and /ui/** -- static resources (/css/**,
 * /js/**) stay unprotected since they carry no sensitive data and a
 * &lt;link&gt;/&lt;script&gt; tag can't attach a token itself.
 */
public class JwtAuthFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(JwtAuthFilter.class);
    private static final String TOKEN_COOKIE_NAME = "cm_token";

    private final SecretKey key;
    private final boolean secretConfigured;

    public JwtAuthFilter(String secret) {
        this.secretConfigured = secret != null && !secret.isBlank();
        if (secretConfigured) {
            this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        } else {
            this.key = null;
            log.warn(
                    "security.jwt-secret (JWT_SECRET) is not set -- every request to /api/** and "
                            + "/ui/** will be rejected as unauthenticated until it's set to the same "
                            + "value StoryForge's backend uses.");
        }
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        // Browsers never attach custom headers (or this app's ?token= convention) to a
        // CORS preflight -- let it through so the actual request's CORS negotiation succeeds.
        if (HttpMethod.OPTIONS.matches(request.getMethod())) {
            chain.doFilter(request, response);
            return;
        }

        if (!secretConfigured) {
            unauthorized(response, "Server has no JWT_SECRET configured");
            return;
        }

        String token = extractToken(request);
        if (token == null) {
            unauthorized(response, "Not authenticated");
            return;
        }

        try {
            Claims claims = Jwts.parser().verifyWith(key).build().parseSignedClaims(token).getPayload();
            request.setAttribute("username", claims.getSubject());
            request.setAttribute("role", claims.get("role", String.class));
        } catch (JwtException e) {
            unauthorized(response, "Invalid or expired token");
            return;
        }

        if (extractCookieToken(request) == null) {
            Cookie cookie = new Cookie(TOKEN_COOKIE_NAME, token);
            cookie.setHttpOnly(true);
            cookie.setPath("/");
            response.addCookie(cookie);
        }

        chain.doFilter(request, response);
    }

    private String extractToken(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            return header.substring("Bearer ".length());
        }
        String cookieToken = extractCookieToken(request);
        if (cookieToken != null) {
            return cookieToken;
        }
        String queryToken = request.getParameter("token");
        if (queryToken != null && !queryToken.isBlank()) {
            return queryToken;
        }
        return null;
    }

    private String extractCookieToken(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        for (Cookie cookie : cookies) {
            if (TOKEN_COOKIE_NAME.equals(cookie.getName()) && !cookie.getValue().isBlank()) {
                return cookie.getValue();
            }
        }
        return null;
    }

    private void unauthorized(HttpServletResponse response, String message) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"" + message + "\"}");
    }
}
