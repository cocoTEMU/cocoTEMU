/*
 * AXI4-Lite slave with 4 x 32-bit read/write registers.
 *
 * Register map:
 *   0x00  REG0
 *   0x04  REG1
 *   0x08  REG2
 *   0x0C  REG3
 *
 * Signal prefix: s_axil_*  (cocotbext-axi convention)
 * Reset: active-low aresetn
 */

module axi_lite_regs #(
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 4,
    parameter STRB_WIDTH = DATA_WIDTH / 8
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    /* Write address channel */
    input  wire [ADDR_WIDTH-1:0]   s_axil_awaddr,
    input  wire [2:0]              s_axil_awprot,
    input  wire                    s_axil_awvalid,
    output reg                     s_axil_awready,

    /* Write data channel */
    input  wire [DATA_WIDTH-1:0]   s_axil_wdata,
    input  wire [STRB_WIDTH-1:0]   s_axil_wstrb,
    input  wire                    s_axil_wvalid,
    output reg                     s_axil_wready,

    /* Write response channel */
    output reg  [1:0]              s_axil_bresp,
    output reg                     s_axil_bvalid,
    input  wire                    s_axil_bready,

    /* Read address channel */
    input  wire [ADDR_WIDTH-1:0]   s_axil_araddr,
    input  wire [2:0]              s_axil_arprot,
    input  wire                    s_axil_arvalid,
    output reg                     s_axil_arready,

    /* Read data channel */
    output reg  [DATA_WIDTH-1:0]   s_axil_rdata,
    output reg  [1:0]              s_axil_rresp,
    output reg                     s_axil_rvalid,
    input  wire                    s_axil_rready
);

    localparam NUM_REGS  = 4;
    localparam WORD_BITS = $clog2(STRB_WIDTH);  // 2 for 32-bit

    reg [DATA_WIDTH-1:0] regs [0:NUM_REGS-1];

    /* Latched write address/data */
    reg [ADDR_WIDTH-1:0] aw_addr;
    reg                   aw_valid;
    reg [DATA_WIDTH-1:0]  w_data;
    reg [STRB_WIDTH-1:0]  w_strb;
    reg                   w_valid;

    integer i;

    /* ----- Write address channel ----- */
    always @(posedge aclk) begin
        if (!aresetn) begin
            s_axil_awready <= 1'b0;
            aw_valid       <= 1'b0;
        end else begin
            if (!aw_valid && s_axil_awvalid && (!w_valid || s_axil_wvalid)) begin
                s_axil_awready <= 1'b1;
                aw_addr        <= s_axil_awaddr;
                aw_valid       <= 1'b1;
            end else begin
                s_axil_awready <= 1'b0;
            end
            if (s_axil_bvalid && s_axil_bready)
                aw_valid <= 1'b0;
        end
    end

    /* ----- Write data channel ----- */
    always @(posedge aclk) begin
        if (!aresetn) begin
            s_axil_wready <= 1'b0;
            w_valid       <= 1'b0;
        end else begin
            if (!w_valid && s_axil_wvalid && (!aw_valid || s_axil_awvalid)) begin
                s_axil_wready <= 1'b1;
                w_data        <= s_axil_wdata;
                w_strb        <= s_axil_wstrb;
                w_valid       <= 1'b1;
            end else begin
                s_axil_wready <= 1'b0;
            end
            if (s_axil_bvalid && s_axil_bready)
                w_valid <= 1'b0;
        end
    end

    /* ----- Write response + register update ----- */
    always @(posedge aclk) begin
        if (!aresetn) begin
            s_axil_bvalid <= 1'b0;
            s_axil_bresp  <= 2'b00;
            for (i = 0; i < NUM_REGS; i = i + 1)
                regs[i] <= {DATA_WIDTH{1'b0}};
        end else begin
            if (aw_valid && w_valid && !s_axil_bvalid) begin
                /* Apply byte-lane strobes */
                for (i = 0; i < STRB_WIDTH; i = i + 1) begin
                    if (w_strb[i])
                        regs[aw_addr[ADDR_WIDTH-1:WORD_BITS]][i*8 +: 8] <= w_data[i*8 +: 8];
                end
                s_axil_bresp  <= 2'b00;  // OKAY
                s_axil_bvalid <= 1'b1;
            end else if (s_axil_bvalid && s_axil_bready) begin
                s_axil_bvalid <= 1'b0;
            end
        end
    end

    /* ----- Read address + data ----- */
    always @(posedge aclk) begin
        if (!aresetn) begin
            s_axil_arready <= 1'b0;
            s_axil_rvalid  <= 1'b0;
            s_axil_rdata   <= {DATA_WIDTH{1'b0}};
            s_axil_rresp   <= 2'b00;
        end else begin
            if (s_axil_arvalid && !s_axil_rvalid && !s_axil_arready) begin
                s_axil_arready <= 1'b1;
                s_axil_rdata   <= regs[s_axil_araddr[ADDR_WIDTH-1:WORD_BITS]];
                s_axil_rresp   <= 2'b00;  // OKAY
                s_axil_rvalid  <= 1'b1;
            end else begin
                s_axil_arready <= 1'b0;
                if (s_axil_rvalid && s_axil_rready)
                    s_axil_rvalid <= 1'b0;
            end
        end
    end

endmodule
