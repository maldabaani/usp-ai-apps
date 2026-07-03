package com.jslogicextractor.auth;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;

import javax.crypto.SecretKey;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.Date;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Covers the cm_token cookie bootstrap: a token arriving via ?token= or a
 * Bearer header should mint a cookie (so Thymeleaf's own nav links and this
 * app's own fetch()/EventSource polling stay authenticated on every page past
 * the iframe's first load), but a request that already carries the cookie
 * shouldn't have it re-set on every single request.
 */
class JwtAuthFilterTest {

    private static final String SECRET = "test-secret-at-least-32-bytes-long-for-hs256!!";

    private String validToken(String username, String role) {
        SecretKey key = Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8));
        return Jwts.builder()
                .subject(username)
                .claim("role", role)
                .expiration(new Date(System.currentTimeMillis() + 60_000))
                .signWith(key)
                .compact();
    }

    private HttpServletResponse mockResponse() throws Exception {
        HttpServletResponse response = mock(HttpServletResponse.class);
        when(response.getWriter()).thenReturn(mock(PrintWriter.class));
        return response;
    }

    @Test
    void queryParamTokenWithNoCookieMintsCookieAndProceeds() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter(SECRET);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn(null);
        when(request.getCookies()).thenReturn(null);
        String token = validToken("admin", "admin");
        when(request.getParameter("token")).thenReturn(token);

        filter.doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(request).setAttribute("username", "admin");
        verify(request).setAttribute("role", "admin");

        var cookieCaptor = org.mockito.ArgumentCaptor.forClass(Cookie.class);
        verify(response).addCookie(cookieCaptor.capture());
        assertEquals("cm_token", cookieCaptor.getValue().getName());
        assertEquals(token, cookieCaptor.getValue().getValue());
        assertEquals(true, cookieCaptor.getValue().isHttpOnly());
    }

    @Test
    void bearerHeaderTokenWithNoCookieAlsoMintsCookie() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter(SECRET);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("GET");
        String token = validToken("alice", "user");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + token);
        when(request.getCookies()).thenReturn(null);

        filter.doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(response).addCookie(any(Cookie.class));
    }

    @Test
    void requestAlreadyCarryingCookieIsNotReMinted() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter(SECRET);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn(null);
        String token = validToken("admin", "admin");
        when(request.getCookies()).thenReturn(new Cookie[] { new Cookie("cm_token", token) });

        filter.doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(response, never()).addCookie(any(Cookie.class));
    }

    @Test
    void noTokenAnywhereIsRejected() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter(SECRET);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn(null);
        when(request.getCookies()).thenReturn(null);
        when(request.getParameter("token")).thenReturn(null);

        filter.doFilter(request, response, chain);

        verify(chain, never()).doFilter(any(), any());
        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    void blankSecretRejectsEveryRequest() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter("");
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("GET");
        when(request.getParameter("token")).thenReturn(validToken("admin", "admin"));

        filter.doFilter(request, response, chain);

        verify(chain, never()).doFilter(any(), any());
        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    void optionsRequestBypassesAuthEntirely() throws Exception {
        JwtAuthFilter filter = new JwtAuthFilter(SECRET);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mockResponse();
        FilterChain chain = mock(FilterChain.class);

        when(request.getMethod()).thenReturn("OPTIONS");

        filter.doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(response, never()).setStatus(eq(HttpServletResponse.SC_UNAUTHORIZED));
    }
}
