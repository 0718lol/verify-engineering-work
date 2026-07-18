# Cancellation Event Rollout

The new canonical field is `cancellation_reason`. During a rolling deploy, new
producers must remain readable by old consumers, and new consumers must accept
events from old producers. The compatibility field can be removed only after
the rollout window closes.
