package dev.sujendra.aoi.client;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

class AoiClientAutoConfigurationTest {

    private final ApplicationContextRunner runner =
            new ApplicationContextRunner()
                    .withConfiguration(AutoConfigurations.of(AoiClientAutoConfiguration.class));

    @Test
    void noBeanWithoutABaseUrl() {
        runner.run(context -> assertThat(context).doesNotHaveBean(AoiClient.class));
    }

    @Test
    void beanAndPropertiesBindFromAoiClientPrefix() {
        runner.withPropertyValues(
                        "aoi.client.base-url=http://localhost:8000",
                        "aoi.client.api-key=k",
                        "aoi.client.read-timeout=30s")
                .run(
                        context -> {
                            assertThat(context).hasSingleBean(AoiClient.class);
                            AoiClientProperties properties = context.getBean(AoiClientProperties.class);
                            assertThat(properties.getApiKey()).isEqualTo("k");
                            assertThat(properties.getReadTimeout()).isEqualTo(Duration.ofSeconds(30));
                        });
    }

    @Test
    void aUserDefinedBeanWins() {
        AoiClientProperties custom = new AoiClientProperties();
        custom.setBaseUrl("http://example.invalid");
        AoiClient mine = new AoiClient(custom);
        runner.withPropertyValues("aoi.client.base-url=http://localhost:8000")
                .withBean(AoiClient.class, () -> mine)
                .run(context -> assertThat(context.getBean(AoiClient.class)).isSameAs(mine));
    }
}
