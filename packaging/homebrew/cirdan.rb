# Homebrew formula TEMPLATE for Cirdan's zero-Python standalone binary.
#
# This is the source of truth for the formula published to the tap repo
# (github.com/adanb13/homebrew-tap as Formula/cirdan.rb), enabling:
#   brew install adanb13/tap/cirdan
#
# The `update-homebrew` job in .github/workflows/release-npm.yml fills the
# version + per-platform sha256 placeholders from the release assets and pushes
# the result to the tap on every release. The placeholder values below are
# sentinels the job rewrites — do not treat them as real.
class Cirdan < Formula
  desc "AI infrastructure cartographer and MCP server"
  homepage "https://github.com/adanb13/cirdan"
  version "0.0.0"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.0.0/cirdan-darwin-arm64"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/adanb13/cirdan/releases/download/v0.0.0/cirdan-linux-arm64"
      sha256 "1111111111111111111111111111111111111111111111111111111111111111"
    end
    on_intel do
      url "https://github.com/adanb13/cirdan/releases/download/v0.0.0/cirdan-linux-x64"
      sha256 "2222222222222222222222222222222222222222222222222222222222222222"
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
