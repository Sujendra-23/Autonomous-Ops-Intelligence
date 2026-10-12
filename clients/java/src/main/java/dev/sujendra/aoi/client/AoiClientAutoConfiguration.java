package dev.sujendra.aoi.client;

import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

/** Registers an {@link AoiClient} bean when {@code aoi.client.base-url} is set. */
@AutoConfiguration
@EnableConfigurationProperties(AoiClientProperties.class)
@ConditionalOnProperty(prefix = "aoi.client", name = "base-url")
public class AoiClientAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    AoiClient aoiClient(AoiClientProperties properties) {
        return new AoiClient(properties);
    }
}
