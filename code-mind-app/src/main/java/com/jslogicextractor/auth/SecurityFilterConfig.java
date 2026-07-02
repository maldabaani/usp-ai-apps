package com.jslogicextractor.auth;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import org.springframework.web.filter.CorsFilter;

import java.util.List;

/**
 * Registers the CORS filter and {@link JwtAuthFilter} as plain servlet
 * filters (not bean-scanned {@code @Component}s) so {@code @WebMvcTest}
 * slices -- which don't import arbitrary application {@code @Configuration}
 * classes -- stay unaffected by either. CORS must run before the JWT filter:
 * a browser's CORS preflight (OPTIONS) never carries the app's Authorization
 * header or ?token= param, so it has to be resolved (and let through) before
 * auth is even considered -- JwtAuthFilter itself also short-circuits OPTIONS
 * as a second line of defense.
 */
@Configuration
public class SecurityFilterConfig {

    @Bean
    public FilterRegistrationBean<CorsFilter> corsFilter(
            @Value("${security.cors-origins:http://localhost:4200}") String corsOrigins) {
        CorsConfiguration configuration = new CorsConfiguration();
        configuration.setAllowedOrigins(List.of(corsOrigins.split(",")));
        configuration.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        configuration.setAllowedHeaders(List.of("*"));
        configuration.setAllowCredentials(true);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", configuration);

        FilterRegistrationBean<CorsFilter> registration =
                new FilterRegistrationBean<>(new CorsFilter(source));
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }

    @Bean
    public FilterRegistrationBean<JwtAuthFilter> jwtAuthFilter(
            @Value("${security.jwt-secret:}") String jwtSecret) {
        FilterRegistrationBean<JwtAuthFilter> registration =
                new FilterRegistrationBean<>(new JwtAuthFilter(jwtSecret));
        registration.addUrlPatterns("/api/*", "/ui/*");
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE + 1);
        return registration;
    }
}
