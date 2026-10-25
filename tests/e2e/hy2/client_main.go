// Isolated test fixture. Uses the project's fixed sing-box dependency; does not
// change the published Node source or persist payloads, passwords or raw errors.
package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	box "github.com/sagernet/sing-box"
	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/include"
	"github.com/sagernet/sing-box/option"
	singJSON "github.com/sagernet/sing/common/json"
	M "github.com/sagernet/sing/common/metadata"
)

type FlowResult struct {
	Kind               string `json:"kind"`
	Passed             bool   `json:"passed"`
	ErrorClass         string `json:"error_class,omitempty"`
	DialCount          int    `json:"dial_count"`
	Rounds             int    `json:"rounds"`
	Bytes              uint64 `json:"verified_bytes"`
	ElapsedMS          int64  `json:"elapsed_ms"`
	MaximumRoundTripMS int64  `json:"maximum_round_trip_ms"`
}

type Result struct {
	Passed          bool         `json:"passed"`
	ExpectedFailure bool         `json:"expected_failure"`
	Stage           string       `json:"stage"`
	ErrorClass      string       `json:"error_class,omitempty"`
	ServerPorts     any          `json:"server_ports,omitempty"`
	HopInterval     any          `json:"hop_interval,omitempty"`
	TLSVerification bool         `json:"tls_verification"`
	Flows           []FlowResult `json:"flows,omitempty"`
}

func classify(err error) string {
	if err == nil {
		return ""
	}
	text := strings.ToLower(err.Error())
	for _, p := range []struct{ key, class string }{
		{"certificate", "certificate"}, {"unknown authority", "certificate"},
		{"authentication", "authentication"}, {"auth", "authentication"},
		{"timeout", "timeout"}, {"deadline", "timeout"},
		{"refused", "connection_refused"}, {"unreachable", "network_unreachable"},
		{"mismatch", "payload_mismatch"}, {"closed", "connection_closed"},
		{"eof", "connection_closed"},
	} {
		if strings.Contains(text, p.key) {
			return p.class
		}
	}
	return "operation_failed"
}

func output(r Result) { _ = json.NewEncoder(os.Stdout).Encode(r) }

func ioDeadline(ctx context.Context) time.Time {
	deadline := time.Now().Add(8 * time.Second)
	if overall, ok := ctx.Deadline(); ok && overall.Before(deadline) {
		return overall
	}
	return deadline
}

func tcpFlow(ctx context.Context, outbound adapter.Outbound, target string, duration, interval time.Duration) FlowResult {
	r := FlowResult{Kind: "tcp", DialCount: 1}
	start := time.Now()
	conn, err := outbound.DialContext(ctx, "tcp", M.ParseSocksaddr(target))
	if err != nil {
		r.ErrorClass = classify(err)
		r.ElapsedMS = time.Since(start).Milliseconds()
		return r
	}
	defer conn.Close()
	deadline := time.Now().Add(duration)
	data := make([]byte, 32768)
	reply := make([]byte, len(data))
	for time.Now().Before(deadline) {
		for i := range data {
			data[i] = byte(i + r.Rounds*17)
		}
		binary.BigEndian.PutUint64(data[:8], uint64(r.Rounds))
		_ = conn.SetDeadline(ioDeadline(ctx))
		t0 := time.Now()
		if _, err = io.CopyN(conn, bytes.NewReader(data), int64(len(data))); err != nil {
			break
		}
		if _, err = io.ReadFull(conn, reply); err != nil {
			break
		}
		if !bytes.Equal(data, reply) {
			err = errors.New("payload mismatch")
			break
		}
		elapsed := time.Since(t0).Milliseconds()
		if elapsed > r.MaximumRoundTripMS {
			r.MaximumRoundTripMS = elapsed
		}
		r.Rounds++
		r.Bytes += uint64(len(data))
		select {
		case <-ctx.Done():
			err = ctx.Err()
		case <-time.After(interval):
		}
		if err != nil {
			break
		}
	}
	r.ElapsedMS = time.Since(start).Milliseconds()
	r.ErrorClass = classify(err)
	r.Passed = err == nil && r.Rounds > 0 && !time.Now().Before(deadline)
	return r
}

func udpFlow(ctx context.Context, outbound adapter.Outbound, target string, duration, interval time.Duration) FlowResult {
	r := FlowResult{Kind: "udp", DialCount: 1}
	start := time.Now()
	destination := M.ParseSocksaddr(target)
	conn, err := outbound.ListenPacket(ctx, destination)
	if err != nil {
		r.ErrorClass = classify(err)
		r.ElapsedMS = time.Since(start).Milliseconds()
		return r
	}
	defer conn.Close()
	deadline := time.Now().Add(duration)
	data := make([]byte, 1024)
	reply := make([]byte, 4096)
	for time.Now().Before(deadline) {
		for i := range data {
			data[i] = byte(i + r.Rounds*29)
		}
		binary.BigEndian.PutUint64(data[:8], uint64(r.Rounds))
		_ = conn.SetDeadline(ioDeadline(ctx))
		t0 := time.Now()
		if _, err = conn.WriteTo(data, destination); err != nil {
			break
		}
		var n int
		n, _, err = conn.ReadFrom(reply)
		if err != nil {
			break
		}
		if !bytes.Equal(data, reply[:n]) {
			err = errors.New("payload mismatch")
			break
		}
		elapsed := time.Since(t0).Milliseconds()
		if elapsed > r.MaximumRoundTripMS {
			r.MaximumRoundTripMS = elapsed
		}
		r.Rounds++
		r.Bytes += uint64(n)
		select {
		case <-ctx.Done():
			err = ctx.Err()
		case <-time.After(interval):
		}
		if err != nil {
			break
		}
	}
	r.ElapsedMS = time.Since(start).Milliseconds()
	r.ErrorClass = classify(err)
	r.Passed = err == nil && r.Rounds > 0 && !time.Now().Before(deadline)
	return r
}

func main() {
	configPath := flag.String("config", "", "Panel-generated full sing-box client JSON or a single Hysteria2 outbound JSON (private)")
	tag := flag.String("outbound", "", "Hysteria2 tag; defaults to first Hysteria2 outbound")
	tcpTarget := flag.String("tcp-target", "", "controlled raw TCP echo server address")
	udpTarget := flag.String("udp-target", "", "controlled UDP echo server address")
	duration := flag.Duration("duration", 24*time.Second, "same-flow test duration")
	overallTimeout := flag.Duration("overall-timeout", 0, "global timeout; omitted defaults to duration + 15 seconds")
	interval := flag.Duration("interval", 200*time.Millisecond, "delay between payloads")
	expectFailure := flag.Bool("expect-failure", false, "success means proxy access was rejected")
	serve := flag.String("socks-listen", "", "optional isolated loopback SOCKS listen address; serve only when targets omitted")
	flag.Parse()
	r := Result{Stage: "config", ExpectedFailure: *expectFailure}
	fail := func(err error) { r.ErrorClass = classify(err); output(r); os.Exit(2) }
	timeoutExplicit := false
	flag.Visit(func(f *flag.Flag) {
		if f.Name == "overall-timeout" {
			timeoutExplicit = true
		}
	})
	if !timeoutExplicit {
		*overallTimeout = *duration + 15*time.Second
	}
	if *configPath == "" || *duration <= 0 || *interval <= 0 || *overallTimeout <= 0 {
		fail(errors.New("invalid arguments"))
	}
	data, err := os.ReadFile(*configPath)
	if err != nil {
		fail(err)
	}
	var raw map[string]any
	if err = json.Unmarshal(data, &raw); err != nil {
		fail(err)
	}
	if raw["type"] == "hysteria2" {
		raw = map[string]any{"outbounds": []any{raw}}
	}
	outbounds, _ := raw["outbounds"].([]any)
	var hy2 map[string]any
	for _, v := range outbounds {
		o, ok := v.(map[string]any)
		if !ok || o["type"] != "hysteria2" {
			continue
		}
		if *tag != "" && o["tag"] != *tag {
			continue
		}
		hy2 = o
		break
	}
	if hy2 == nil {
		fail(errors.New("missing hysteria2 outbound"))
	}
	if hy2["tag"] == nil || hy2["tag"] == "" {
		hy2["tag"] = "hy2-fixture"
	}
	*tag, _ = hy2["tag"].(string)
	r.ServerPorts = hy2["server_ports"]
	r.HopInterval = hy2["hop_interval"]
	tls, _ := hy2["tls"].(map[string]any)
	r.TLSVerification = tls != nil && tls["enabled"] == true && tls["insecure"] != true
	if !r.TLSVerification {
		fail(errors.New("certificate verification required"))
	}
	// Preserve the selected outbound's TLS and hopping fields. Ignore unrelated
	// subscription selectors, URL tests, ingress, DNS and experimental settings
	// so this fixture performs only the requested controlled traffic.
	raw = map[string]any{
		"outbounds": []any{hy2},
		"log":       map[string]any{"disabled": true},
		"inbounds":  []any{},
		"route":     map[string]any{"final": *tag},
	}
	if *serve != "" {
		host, portText, e := net.SplitHostPort(*serve)
		if e != nil {
			fail(e)
		}
		port, e := strconv.Atoi(portText)
		if e != nil {
			fail(e)
		}
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			fail(errors.New("loopback listen required"))
		}
		raw["inbounds"] = []any{map[string]any{"type": "socks", "tag": "fixture-socks", "listen": host, "listen_port": port}}
	}
	data, err = json.Marshal(raw)
	if err != nil {
		fail(err)
	}
	ctx, cancel := context.WithTimeout(include.Context(context.Background()), *overallTimeout)
	defer cancel()
	opts, err := singJSON.UnmarshalExtendedContext[option.Options](ctx, data)
	if err != nil {
		fail(err)
	}
	r.Stage = "startup"
	instance, err := box.New(box.Options{Context: ctx, Options: opts})
	if err != nil {
		fail(err)
	}
	if err = instance.Start(); err != nil {
		instance.Close()
		fail(err)
	}
	defer instance.Close()
	if *tcpTarget == "" && *udpTarget == "" && *serve != "" {
		r.Stage = "socks_ready"
		r.Passed = true
		output(r)
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, os.Interrupt, syscall.SIGTERM)
		select {
		case <-ch:
		case <-ctx.Done():
		}
		return
	}
	if *tcpTarget == "" && *udpTarget == "" {
		fail(errors.New("controlled echo target required"))
	}
	outbound, ok := instance.Outbound().Outbound(*tag)
	if !ok {
		fail(errors.New("missing outbound"))
	}
	r.Stage = "traffic"
	var wg sync.WaitGroup
	var mu sync.Mutex
	appendFlow := func(flow FlowResult) { mu.Lock(); r.Flows = append(r.Flows, flow); mu.Unlock() }
	if *tcpTarget != "" {
		wg.Add(1)
		go func() { defer wg.Done(); appendFlow(tcpFlow(ctx, outbound, *tcpTarget, *duration, *interval)) }()
	}
	if *udpTarget != "" {
		wg.Add(1)
		go func() { defer wg.Done(); appendFlow(udpFlow(ctx, outbound, *udpTarget, *duration, *interval)) }()
	}
	wg.Wait()
	r.Passed = true
	for _, flow := range r.Flows {
		if *expectFailure {
			r.Passed = r.Passed && !flow.Passed && flow.Bytes == 0
		} else {
			r.Passed = r.Passed && flow.Passed
		}
	}
	output(r)
	if !r.Passed {
		os.Exit(1)
	}
}
