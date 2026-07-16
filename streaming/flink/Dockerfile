# Flink 1.20 + the Kafka SQL connector. We run the job as Flink SQL (via
# sql-client), so no PyFlink/pip is needed -- just drop the connector fat-jar into
# Flink's lib. Running Flink in a container is deliberate: it avoids the JVM
# friction of a native Windows install (the same reason native Spark was ruled out).
FROM flink:1.20.0-scala_2.12-java11

ADD https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.3.0-1.20/flink-sql-connector-kafka-3.3.0-1.20.jar \
    /opt/flink/lib/flink-sql-connector-kafka-3.3.0-1.20.jar
RUN chmod 644 /opt/flink/lib/flink-sql-connector-kafka-3.3.0-1.20.jar
