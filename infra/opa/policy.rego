package secure_rag.authz

default allow = {
  "allow": false,
  "reason": "default_deny",
  "policy_version": "1.0"
}

allow = {
  "allow": true,
  "reason": "public_document",
  "policy_version": "1.0"
} {
  input.user.authenticated == true
  input.resource.is_public == true
}

allow = {
  "allow": true,
  "reason": "allowed_user_match",
  "policy_version": "1.0"
} {
  input.user.authenticated == true
  input.user.user_id != ""
  input.resource.allowed_users[_] == input.user.user_id
}

allow = {
  "allow": true,
  "reason": "allowed_group_match",
  "policy_version": "1.0"
} {
  input.user.authenticated == true
  some i
  g := input.user.groups[i]
  input.resource.allowed_groups[_] == g
}

allow = {
  "allow": true,
  "reason": "transitional_allowed_email",
  "policy_version": "1.0"
} {
  input.user.authenticated == true
  input.transitional_drive_acl == true
  input.user.email != ""
  input.resource.allowed_emails[_] == lower(input.user.email)
}

allow = {
  "allow": true,
  "reason": "transitional_allowed_domain",
  "policy_version": "1.0"
} {
  input.user.authenticated == true
  input.transitional_drive_acl == true
  input.user.domain != ""
  input.resource.allowed_domains[_] == lower(input.user.domain)
}
