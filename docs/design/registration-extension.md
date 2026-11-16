# Registration extension contract

`BaseUserViewSet` exposes an opt-in extension for applications that need to
collect a registration value and apply an application-owned grant after email
confirmation. The shared package does not store, interpret, or consume that
value. Applications own their validation rules, persistent binding record,
grant policy, and cleanup.

## Request and preparation

Keep the default `RegistrationRequestSerializer` unchanged. A consumer may
override `get_registration_request_serializer(data=...)` and return a subclass
that declares its optional application fields as `write_only`. Its
`validated_data` fields outside the shared request fields are passed to
`prepare_registration_binding(email, mode, binding_ref, input_data)`.

Preparation runs only after Turnstile, signup-mode and policy checks, access
code validation, email-domain checks, and QR token validation. It runs before
the pending email is sent. The shared view generates a random 256-bit
`binding_ref`; the hook must persist a binding keyed by a one-way hash of that
reference, associating it with the normalized email, an irreversible
representation of the submitted value, and an expiry no later than the
pending-registration token's 24-hour lifetime. It must not grant or consume an
entitlement during preparation. If preparation fails, raise a sanitized DRF
`ValidationError` or an application error handled by the consumer. Never put
the submitted value in an exception, log, URL, signed token, or client storage.

Only the random `binding_ref` is added to the signed confirmation payload. The
default serializer and hooks add no request fields and preserve the existing
registration contract.

## Confirmation and lifecycle

After the signed token is validated and a new user and verified email address
are created, `apply_registration_binding(user, binding_ref)` runs inside the
same `transaction.atomic()` block as user creation, generic access-code
consumption, QR redemption, and registration context application. It must
look up the hash of the reference, verify that the record is unexpired and
bound to `user.email`, then atomically mark the binding consumed and apply the
grant. Use a row lock or compare-and-swap so concurrent confirmations cannot
both consume it. Any failure must raise and allow the transaction to roll back
the user, grant, generic access-code use, and QR quota change.

The confirmation link works in another browser because the binding is
identified only by the signed reference. A previously completed account gets
the existing privacy-preserving success response; the application hook is not
called. A second confirmation cannot reapply the grant. Expired or invalid
pending tokens keep the existing `Auth.PENDING_TOKEN_EXPIRED` and
`Auth.PENDING_TOKEN_INVALID` responses. Consumer failures should use a
sanitized error and must not disclose account existence or binding contents.

The consumer owns deletion/expiry cleanup of unconfirmed bindings and any
recovery of records left behind when email delivery fails. The shared package
does not add a schema or retain the submitted value.
