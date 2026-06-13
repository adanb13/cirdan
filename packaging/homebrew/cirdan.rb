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
  version "0.8.1"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.8.1/cirdan-darwin-arm64"
      sha256 "e26cabac4a090ea35fe6d3c2fdd9ffde9074fd8bdeb552515977fdf8c1cae5f1"
    end
    # Intel macOS (darwin-x64) is no longer built — Apple Silicon only.
  end

  on_linux do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.8.1/cirdan-linux-arm64"
      sha256 "5758fbe9d911dca5f65637fe910015e16cb12648a99c20e5c993eeac4c7db9c0"
    end
    on_intel do
      url "https://github.com/adanb13/cirdan/releases/download/v0.8.1/cirdan-linux-x64"
      sha256 "e67a2a97bffb1c89f52fa5d64f7bfe667128aa51906087eb562a81d33d74c311"
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
