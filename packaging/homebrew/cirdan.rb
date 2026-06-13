# Homebrew formula for Cirdan's zero-Python standalone binary.
#
# This lives in a tap repo (e.g. github.com/adanb13/homebrew-tap as Formula/
# cirdan.rb) so users can:  brew install adanb13/tap/cirdan
#
# The url/sha256 per platform point at the release assets uploaded by
# .github/workflows/release-npm.yml (cirdan-<target>). On each release, bump
# `version`, the URLs, and fill the sha256 values (e.g. `shasum -a 256 cirdan-*`).
# A tap update can be automated from the release workflow.
class Cirdan < Formula
  desc "AI infrastructure cartographer and MCP server"
  homepage "https://github.com/adanb13/cirdan"
  version "0.7.0"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.7.0/cirdan-darwin-arm64"
      sha256 "REPLACE_WITH_DARWIN_ARM64_SHA256"
    end
    on_intel do
      url "https://github.com/adanb13/cirdan/releases/download/v0.7.0/cirdan-darwin-x64"
      sha256 "REPLACE_WITH_DARWIN_X64_SHA256"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.7.0/cirdan-linux-arm64"
      sha256 "REPLACE_WITH_LINUX_ARM64_SHA256"
    end
    on_intel do
      url "https://github.com/adanb13/cirdan/releases/download/v0.7.0/cirdan-linux-x64"
      sha256 "REPLACE_WITH_LINUX_X64_SHA256"
    end
  end

  def install
    # The downloaded asset is the bare binary named cirdan-<target>.
    bin.install Dir["cirdan-*"].first => "cirdan"
  end

  test do
    assert_match "cirdan", shell_output("#{bin}/cirdan --version")
  end
end
