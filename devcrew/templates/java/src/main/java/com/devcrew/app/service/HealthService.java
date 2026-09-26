package com.devcrew.app.service;

import com.devcrew.app.dto.HealthResponse;
import org.springframework.stereotype.Service;

@Service
public class HealthService {

    public HealthResponse check() {
        return new HealthResponse("ok");
    }
}
